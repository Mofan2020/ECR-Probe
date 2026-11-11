"""自动化规则的测试。

重点是那个用户明确要的场景：开始屏幕广播 → 自动跟一条切窗口的动作。
规则要能被关掉、能落盘、落盘文件坏了不能把程序带崩。
"""

from __future__ import annotations

import json
import pathlib

from ecr_probe import rules
from ecr_probe.rules import Rule, RuleStore

# ------------------------------------------------------------------ 匹配


def test_exact_match() -> None:
    rule = Rule(trigger="a.b.c", action="x.y")
    assert rule.matches("a.b.c")
    assert not rule.matches("a.b.c.d")
    assert not rule.matches("a.b")


def test_wildcard_match() -> None:
    rule = Rule(trigger="local.teacher->student.*", action="x.y")
    assert rule.matches("local.teacher->student.shutdown")
    assert rule.matches("local.teacher->student.startBroadcast")
    assert not rule.matches("local.student->teacher.x")


def test_empty_trigger_never_matches() -> None:
    assert not Rule(trigger="", action="x").matches("anything")


# ------------------------------------------------------------------ 出厂规则


def test_broadcast_to_window_rule_exists_and_enabled() -> None:
    """用户要的那条：广播开始就切窗口。必须在，且默认开。"""
    found = [
        r for r in rules.default_rules()
        if r.trigger == "local.teacher->student.startBroadcast"
        and "displayMode" in r.params
        and r.enabled
    ]
    assert found, "没有找到默认启用的「广播→切窗口」规则"
    assert found[0].params["displayMode"] == "window"


def test_receive_broadcast_rule_also_present() -> None:
    found = [
        r for r in rules.default_rules()
        if r.trigger == "local.teacher->student.startReceiveBroadcast" and r.enabled
    ]
    assert found


def test_default_rules_have_ids() -> None:
    ids = [r.id for r in rules.default_rules()]
    assert len(ids) == len(set(ids))
    assert all(ids)


def test_dangerous_rules_are_disabled_by_default() -> None:
    for rule in rules.default_rules():
        if rule.action in {"student.desktopBroadcast.close", "local.teacher->student.shutdown"}:
            assert not rule.enabled, f"{rule.action} 不该默认开"


# ------------------------------------------------------------------ 落盘


def test_store_creates_file_with_defaults(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "rules.json"
    store = RuleStore(path)
    assert path.exists()
    assert store.rules
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "rules" in data


def test_store_roundtrip(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "rules.json"
    store = RuleStore(path)
    store.add("a.b", "c.d", {"k": 1}, repeat=2, delay_ms=50, note="n")

    again = RuleStore(path)
    hit = [r for r in again.rules if r.trigger == "a.b"]
    assert hit
    assert hit[0].params == {"k": 1}
    assert hit[0].repeat == 2
    assert hit[0].delay_ms == 50


def test_corrupt_file_falls_back_to_defaults(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = RuleStore(path)
    assert store.rules, "文件坏了也该有出厂规则"


def test_partial_garbage_entries_are_skipped(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps({"rules": [{"trigger": "a", "action": "b"}, {"nope": 1}]}),
        encoding="utf-8",
    )
    store = RuleStore(path)
    assert len(store.rules) == 1
    assert store.rules[0].trigger == "a"


def test_repeat_and_delay_are_clamped(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps({"rules": [{"trigger": "a", "action": "b", "repeat": 0, "delay_ms": -5}]}),
        encoding="utf-8",
    )
    store = RuleStore(path)
    assert store.rules[0].repeat == 1
    assert store.rules[0].delay_ms == 0


# ------------------------------------------------------------------ 操作


def test_toggle(tmp_path: pathlib.Path) -> None:
    store = RuleStore(tmp_path / "rules.json")
    rule = store.rules[0]
    before = rule.enabled
    assert store.toggle(rule.id) is (not before)
    assert store.get(rule.id).enabled is (not before)


def test_toggle_unknown_id() -> None:
    store = RuleStore.__new__(RuleStore)
    store.rules = []
    store.path = pathlib.Path("/dev/null")
    assert store.toggle("nope") is False


def test_remove(tmp_path: pathlib.Path) -> None:
    store = RuleStore(tmp_path / "rules.json")
    first = store.rules[0]
    assert store.remove(first.id)
    assert store.get(first.id) is None
    assert not store.remove(first.id)


def test_reset_restores_defaults(tmp_path: pathlib.Path) -> None:
    store = RuleStore(tmp_path / "rules.json")
    store.rules.clear()
    store.reset()
    assert len(store.rules) == len(rules.default_rules())


def test_for_method_only_returns_enabled(tmp_path: pathlib.Path) -> None:
    store = RuleStore(tmp_path / "rules.json")
    store.rules = [
        Rule(trigger="a.b", action="x.y", enabled=True),
        Rule(trigger="a.b", action="x.z", enabled=False),
    ]
    hits = store.for_method("a.b")
    assert len(hits) == 1
    assert hits[0].action == "x.y"


def test_for_method_matches_multiple() -> None:
    store = RuleStore.__new__(RuleStore)
    store.path = pathlib.Path("/dev/null")
    store.rules = [
        Rule(trigger="local.teacher->student.startBroadcast", action="a"),
        Rule(trigger="local.teacher->student.*", action="b"),
    ]
    assert len(store.for_method("local.teacher->student.startBroadcast")) == 2


def test_unwritable_path_does_not_raise(tmp_path: pathlib.Path) -> None:
    """落盘失败不该让程序崩。"""
    store = RuleStore(tmp_path / "rules.json")
    store.path = pathlib.Path("/this/path/does/not/exist/rules.json")
    store.save()
