"""桥接层测试：端到端跑通「连上靶子 → 发命令 → 规则自动跟一条」。

这个测试是工具能不能用的核心证据。没有它，「自动化」就只是界面上的字。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from ecr_probe import guard
from ecr_probe.bridge import Bridge
from ecr_probe.mockserver import MockServer
from ecr_probe.rules import Rule


@pytest.fixture()
def bridge(tmp_path: pathlib.Path, acked_gate: pathlib.Path) -> Bridge:
    """规则落在临时目录，闸门已经过掉——绝不碰用户真实的 ~/.ecr-probe。"""
    return Bridge(rule_path=tmp_path / "rules.json")


@pytest.fixture()
def server() -> MockServer:
    srv = MockServer(host="127.0.0.1", port=0, path="/").start()
    yield srv
    srv.stop()


# ------------------------------------------------------------------ 守卫


def test_connect_refuses_remote_host(bridge: Bridge) -> None:
    res = bridge.connect("8.8.8.8", 80, "/")
    assert not res["ok"]
    assert "不是本机地址" in res["error"]


def test_connect_refuses_lan_host_not_on_this_machine(bridge: Bridge) -> None:
    res = bridge.connect("192.168.1.32", 1234, "/")
    assert not res["ok"]
    assert "不是本机地址" in res["error"]


def test_locate_refuses_remote_host(bridge: Bridge) -> None:
    res = bridge.locate("203.0.113.5", "80")
    assert not res["ok"]
    assert "不是本机地址" in res["error"]


def test_probe_one_refuses_remote(bridge: Bridge) -> None:
    res = bridge.probe_one("1.1.1.1", 443)
    assert not res["ok"]


# ------------------------------------------------------------------ 连接与发送


def test_connect_and_send(bridge: Bridge, server: MockServer) -> None:
    conn = bridge.connect("127.0.0.1", server.port, "/")
    assert conn["ok"], conn
    assert conn["target"].startswith("ws://127.0.0.1:")

    res = bridge.send("local.teacher->student.turnOffMute", '{"teacherUid": "t1"}')
    assert res["ok"], res

    assert server.received
    sent = json.loads(server.received[0])
    assert sent["method"] == "local.teacher->student.turnOffMute"
    assert sent["params"]["teacherUid"] == "t1"


def test_send_without_connection_fails_cleanly(bridge: Bridge) -> None:
    res = bridge.send("a.b", "")
    assert not res["ok"]
    assert "还没连上" in res["error"]
    assert res["payload"], "失败时也要给出报文，方便排查"


def test_bad_params_json_is_reported(bridge: Bridge, server: MockServer) -> None:
    bridge.connect("127.0.0.1", server.port, "/")
    res = bridge.send("a.b", "{not json")
    assert not res["ok"]
    assert "JSON" in res["error"]


def test_non_object_params_refused(bridge: Bridge, server: MockServer) -> None:
    bridge.connect("127.0.0.1", server.port, "/")
    res = bridge.send("a.b", "[1,2,3]")
    assert not res["ok"]


# ------------------------------------------------------------------ 自动化


def test_rule_fires_after_send(bridge: Bridge, server: MockServer) -> None:
    """核心场景：广播一开始，规则自动补一条切窗口的命令。"""
    bridge.rules.rules = [
        Rule(
            trigger="local.teacher->student.startBroadcast",
            action="local.teacher->student.setProperty",
            params={"displayMode": "window"},
            repeat=2,
            delay_ms=0,
        )
    ]
    bridge.connect("127.0.0.1", server.port, "/")

    res = bridge.send("local.teacher->student.startBroadcast", "{}")
    assert res["ok"]
    assert res["fired"], "规则没触发"
    assert res["fired"][0]["ok"]

    # 靶子应该收到：1 条触发 + 2 条动作
    methods = [json.loads(m)["method"] for m in server.received]
    assert methods.count("local.teacher->student.setProperty") == 2
    assert methods[0] == "local.teacher->student.startBroadcast"


def test_rule_action_carries_params(bridge: Bridge, server: MockServer) -> None:
    bridge.rules.rules = [
        Rule(trigger="a.b", action="c.d", params={"displayMode": "window", "k": 2})
    ]
    bridge.connect("127.0.0.1", server.port, "/")
    bridge.send("a.b", "")

    actions = [json.loads(m) for m in server.received if json.loads(m)["method"] == "c.d"]
    assert len(actions) == 1
    assert actions[0]["params"]["displayMode"] == "window"
    assert actions[0]["params"]["k"] == 2


def test_rule_does_not_cascade(bridge: Bridge, server: MockServer) -> None:
    """规则动作不再触发规则，否则 a→b→a 会打转。"""
    bridge.rules.rules = [
        Rule(trigger="a", action="b"),
        Rule(trigger="b", action="a"),
    ]
    bridge.connect("127.0.0.1", server.port, "/")
    bridge.send("a", "")

    methods = [json.loads(m)["method"] for m in server.received]
    assert methods == ["a", "b"], methods


def test_disabled_rule_does_not_fire(bridge: Bridge, server: MockServer) -> None:
    bridge.rules.rules = [Rule(trigger="a.b", action="c.d", enabled=False)]
    bridge.connect("127.0.0.1", server.port, "/")
    res = bridge.send("a.b", "")
    assert res["ok"]
    assert not res["fired"]


def test_no_rule_means_no_extra_traffic(bridge: Bridge, server: MockServer) -> None:
    bridge.rules.rules = []
    bridge.connect("127.0.0.1", server.port, "/")
    bridge.send("a.b", "")
    assert len(server.received) == 1


# ------------------------------------------------------------------ 规则接口


def test_get_rules_lists_defaults(bridge: Bridge) -> None:
    res = bridge.get_rules()
    assert res["ok"]
    assert len(res["rules"]) >= 2
    assert any("startBroadcast" in r["trigger"] for r in res["rules"])


def test_add_and_remove_rule(bridge: Bridge) -> None:
    res = bridge.add_rule("x.y", "z.w", '{"k":1}', 3, 100)
    assert res["ok"]
    rule_id = res["rule"]
    assert any(r["id"] == rule_id for r in res["rules"])

    removed = bridge.remove_rule(rule_id)
    assert removed["ok"]
    assert not any(r["id"] == rule_id for r in removed["rules"])


def test_add_rule_rejects_empty(bridge: Bridge) -> None:
    assert not bridge.add_rule("", "z.w")["ok"]
    assert not bridge.add_rule("x.y", "")["ok"]


def test_add_rule_rejects_bad_params(bridge: Bridge) -> None:
    assert not bridge.add_rule("x.y", "z.w", "broke")["ok"]


def test_toggle_rule(bridge: Bridge) -> None:
    rule_id = bridge.get_rules()["rules"][0]["id"]
    res = bridge.toggle_rule(rule_id)
    assert res["ok"]
    assert isinstance(res["enabled"], bool)


def test_reset_rules(bridge: Bridge) -> None:
    bridge.add_rule("x.y", "z.w")
    res = bridge.reset_rules()
    assert res["ok"]
    assert not any(r["trigger"] == "x.y" for r in res["rules"])


# ------------------------------------------------------------------ 命令目录


def test_get_commands_covers_everything(bridge: Bridge) -> None:
    res = bridge.get_commands()
    assert res["ok"]
    total = sum(len(g["items"]) for g in res["groups"])
    assert total == res["total"]
    assert res["total"] > 100


def test_every_command_has_name(bridge: Bridge) -> None:
    res = bridge.get_commands()
    for group in res["groups"]:
        for item in group["items"]:
            assert item["name"]
            assert isinstance(item["params"], dict)


# ------------------------------------------------------------------ 日志


def test_log_records_actions(bridge: Bridge, server: MockServer) -> None:
    bridge.connect("127.0.0.1", server.port, "/")
    bridge.send("a.b", "")
    lines = bridge.get_log()["lines"]
    assert any("已连上" in line for line in lines)
    assert any("发出 a.b" in line for line in lines)


def test_clear_log(bridge: Bridge) -> None:
    bridge.send("a.b", "")
    assert bridge.get_log()["lines"]
    bridge.clear_log()
    assert not bridge.get_log()["lines"]


# ------------------------------------------------------------------ 闸门与策略


def test_send_refused_before_gate(tmp_path: pathlib.Path, server: MockServer) -> None:
    """没过闸门，连得上也不许发。"""
    from ecr_probe import gate

    bare = Bridge(rule_path=tmp_path / "rules.json")
    assert not gate.gate_passed()
    bare.connect("127.0.0.1", server.port, "/")
    res = bare.send("local.teacher->student.turnOnMute", "")
    assert not res["ok"]
    assert "闸门" in res["error"]
    assert not server.received


def test_risky_command_blocked_while_locked(bridge: Bridge, server: MockServer) -> None:
    bridge.connect("127.0.0.1", server.port, "/")
    res = bridge.send("local.teacher->student.shutdown", "")
    assert not res["ok"]
    assert res.get("blocked")
    assert "allow-dangerous" in res["error"]
    assert not server.received


def test_risky_command_allowed_after_unlock(bridge: Bridge, server: MockServer) -> None:
    from ecr_probe import gate

    bridge.connect("127.0.0.1", server.port, "/")
    assert bridge.set_dangerous(True)["ok"]
    res = bridge.send("local.teacher->student.shutdown", "")
    assert res["ok"], res
    assert server.received
    gate.set_dangerous_allowed(False)


def test_harmless_command_works_while_locked(bridge: Bridge, server: MockServer) -> None:
    bridge.connect("127.0.0.1", server.port, "/")
    res = bridge.send("local.teacher->student.turnOnMute", "")
    assert res["ok"]


def test_risky_rule_action_blocked(bridge: Bridge, server: MockServer) -> None:
    """规则也不能绕过策略去发关机。"""
    from ecr_probe.rules import Rule

    bridge.rules.rules = [Rule(trigger="a.b", action="local.teacher->student.shutdown")]
    bridge.connect("127.0.0.1", server.port, "/")
    res = bridge.send("a.b", "")
    assert res["ok"]
    assert res["fired"], "规则应该记下被拦这件事"
    assert res["fired"][0]["blocked"] is True
    methods = [json.loads(m)["method"] for m in server.received]
    assert methods == ["a.b"], "关机命令不该真的发出去"


def test_gate_status_reports_challenge(bridge: Bridge) -> None:
    gs = bridge.gate_status()
    assert gs["ok"]
    assert gs["passed"] is True
    assert gs["challenge"] == __import__("ecr_probe.gate", fromlist=["CHALLENGE"]).CHALLENGE


def test_pass_gate_wrong_then_right(bridge: Bridge) -> None:
    from ecr_probe import gate

    gate.reset_gate()
    assert not bridge.pass_gate("瞎写的")["ok"]
    assert not gate.gate_passed()
    assert bridge.pass_gate(gate.CHALLENGE)["ok"]
    assert gate.gate_passed()


def test_commands_report_risky_flag(bridge: Bridge) -> None:
    items = [i for g in bridge.get_commands()["groups"] for i in g["items"]]
    assert any(i["risky"] for i in items)
    assert any(not i["risky"] for i in items)


def test_guard_used_by_every_entry_point() -> None:
    """守卫生效的证明：这几个函数都必须抛 NonLocalError。"""
    for call in (
        lambda: guard.guard_target("8.8.8.8", 80),
        lambda: guard.guard_target("192.168.1.32", 80),
        lambda: guard.guard_host("*"),
    ):
        with pytest.raises(guard.NonLocalError):
            call()
