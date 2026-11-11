"""命令目录与报文构造的测试。

目录是对外承诺的一部分（README 里写了多少条就是多少条），
拼写错误是逆向对接的关键细节，都要钉住。
"""

from __future__ import annotations

import json

from ecr_probe import commands


def test_local_command_count() -> None:
    """44 条，是从字符串表去重后的实测数字。改目录就得改这个数。"""
    assert len(commands.LOCAL_COMMANDS) == 44


def test_no_duplicate_command_names() -> None:
    names = [c.name for c in commands.ALL_COMMANDS]
    assert len(names) == len(set(names)), "命令名重复了"


def test_local_commands_are_all_teacher_or_student() -> None:
    for cmd in commands.LOCAL_COMMANDS:
        assert cmd.name.startswith("local.")
        assert cmd.direction, f"{cmd.name} 没有 -> 方向"
        src = cmd.direction.split("->")[0]
        assert src in {"teacher", "student"}, cmd.name


def test_original_spelling_typos_preserved() -> None:
    """原二进制里的拼写错误必须照抄，'修正'了就对接不上。"""
    names = {c.name for c in commands.ALL_COMMANDS}
    assert "student.classromm.bindResultNotify" in names
    assert "teacher.managent.getStudentAPPList" in names
    # 反过来确认没有"修正"过的版本混进来
    assert "student.classroom.bindResultNotify" not in names
    assert "teacher.management.getStudentAPPList" not in names


def test_high_risk_commands_present() -> None:
    """这些是安全分析里点到的，目录里必须在。"""
    names = {c.name for c in commands.ALL_COMMANDS}
    for expected in (
        "local.teacher->student.shutdown",
        "local.teacher->student.reboot",
        "local.teacher->student.remotecontrol.keyboardMouse",
        "local.teacher->student.StartNetworkControl",
        "local.teacher->student.UpdateProcessControlPolicy",
        "local.teacher->student.turnOffScreen",
        "local.teacher->student.turnOffMute",
    ):
        assert expected in names


# ------------------------------------------------------------------ 报文信封


def test_envelope_shape() -> None:
    req = commands.build_request("local.teacher->student.turnOnScreen", {"teachingUid": "t1"})
    body = json.loads(req.payload)
    assert set(body) == {"method", "params", "traceId"}
    assert body["method"] == "local.teacher->student.turnOnScreen"
    assert body["params"]["teachingUid"] == "t1"
    assert isinstance(body["traceId"], str) and body["traceId"]


def test_timestamp_is_injected() -> None:
    req = commands.build_request("a.b")
    assert "timestamp" in req.params
    assert isinstance(req.params["timestamp"], int)
    assert req.params["timestamp"] > 1_600_000_000_000  # 毫秒


def test_explicit_param_survives() -> None:
    req = commands.build_request("a.b", {"timestamp": 1})
    assert req.params["timestamp"] == 1


def test_trace_id_unique() -> None:
    a = commands.build_request("a.b")
    b = commands.build_request("a.b")
    assert a.trace_id != b.trace_id


def test_empty_params_ok() -> None:
    req = commands.build_request("local.teacher->student.shutdown")
    body = json.loads(req.payload)
    assert body["params"]["timestamp"]


def test_pretty_is_valid_json() -> None:
    req = commands.build_request("a.b", {"k": "中文"})
    assert json.loads(req.pretty())["params"]["k"] == "中文"


def test_unicode_not_escaped() -> None:
    req = commands.build_request("a.b", {"name": "老师"})
    assert "老师" in req.payload


# ------------------------------------------------------------------ 响应解析


def test_parse_normal_response() -> None:
    out = commands.parse_response('{"code":0,"message":"ok","data":{}}')
    assert out["code"] == 0
    assert out["parsed"] is True


def test_parse_garbage_does_not_raise() -> None:
    out = commands.parse_response("\x00\x01not json")
    assert out["parsed"] is False
    assert "raw" in out


def test_parse_non_object_json() -> None:
    out = commands.parse_response("[1,2,3]")
    assert out["parsed"] is False


def test_groups_cover_all_commands() -> None:
    total = sum(len(v) for v in commands.GROUPS.values())
    assert total == len(commands.ALL_COMMANDS)
