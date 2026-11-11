"""闸门与危险命令策略的测试。

这两层不是安全机制，是摩擦。但摩擦也得真的摩擦得住——写错一个字符
就放行的话，它连摩擦都不是。
"""

from __future__ import annotations

import pathlib

import pytest

from ecr_probe import commands, gate

# ------------------------------------------------------------------ 闸门


def test_gate_starts_closed() -> None:
    assert not gate.gate_passed()


def test_wrong_phrase_does_not_pass() -> None:
    assert not gate.pass_gate("我确认")
    assert not gate.pass_gate("")
    assert not gate.pass_gate(gate.CHALLENGE + "。")
    assert not gate.gate_passed()


def test_near_miss_does_not_pass() -> None:
    """少一个字、换个标点都不行。"""
    broken = gate.CHALLENGE.replace("全部", "所有")
    assert not gate.pass_gate(broken)


def test_correct_phrase_passes() -> None:
    assert gate.pass_gate(gate.CHALLENGE)
    assert gate.gate_passed()


def test_surrounding_whitespace_tolerated() -> None:
    """粘贴时常带换行和空格，这个不该算错。"""
    assert gate.pass_gate(f"  {gate.CHALLENGE}\n")
    assert gate.gate_passed()


def test_gate_file_written(isolated_ecr_home: pathlib.Path) -> None:
    gate.pass_gate(gate.CHALLENGE)
    path = isolated_ecr_home / "gate.json"
    assert path.exists()
    assert "digest" in path.read_text(encoding="utf-8")


def test_corrupt_gate_file_means_closed(isolated_ecr_home: pathlib.Path) -> None:
    gate.pass_gate(gate.CHALLENGE)
    (isolated_ecr_home / "gate.json").write_text("{broken", encoding="utf-8")
    assert not gate.gate_passed()


def test_wrong_version_means_closed(isolated_ecr_home: pathlib.Path) -> None:
    import json

    (isolated_ecr_home / "gate.json").write_text(
        json.dumps({"version": 999, "digest": "x"}), encoding="utf-8"
    )
    assert not gate.gate_passed()


def test_reset_gate_recloses() -> None:
    gate.pass_gate(gate.CHALLENGE)
    gate.reset_gate()
    assert not gate.gate_passed()


def test_challenge_is_not_trivial() -> None:
    """确认语得有实质内容，不能是 "yes" 那种一点就过的东西。"""
    assert len(gate.CHALLENGE) >= 15
    assert gate.CHALLENGE not in {"yes", "ok", "1", "确认", "我同意"}


# ------------------------------------------------------------------ 危险命令


def test_dangerous_locked_by_default() -> None:
    assert not gate.dangerous_allowed()


def test_risky_classification() -> None:
    for name in (
        "local.teacher->student.shutdown",
        "local.teacher->student.reboot",
        "local.teacher->student.StartNetworkControl",
        "local.teacher->student.UpdateProcessControlPolicy",
        "local.teacher->student.remotecontrol.keyboardMouse",
        "classroom.management.shutdown",
        "thing.service.reboot",
        "student.desktop.previewStartRecord",
        "local.teacher->student.turnOffScreen",
    ):
        assert commands.is_risky(name), f"{name} 应该算危险"


def test_harmless_commands_are_not_risky() -> None:
    for name in (
        "local.teacher->student.turnOnScreen",
        "local.teacher->student.turnOnMute",
        "local.teacher->student.startBroadcast",
        "local.teacher->student.setProperty",
        "local.teacher->student.notifyStart",
    ):
        assert not commands.is_risky(name), f"{name} 不该算危险"


def test_risky_count_is_sane() -> None:
    """全部锁定 vs 一条不锁，两个极端都不对。"""
    risky = [c for c in commands.ALL_COMMANDS if c.risky]
    assert 15 <= len(risky) <= 80, f"被标危险的有 {len(risky)} 条，数字不对劲"


def test_blocked_when_locked() -> None:
    with pytest.raises(gate.Blocked, match="allow-dangerous"):
        gate.check_command("local.teacher->student.shutdown", risky=True)


def test_allowed_when_unlocked() -> None:
    gate.set_dangerous_allowed(True)
    gate.check_command("local.teacher->student.shutdown", risky=True)


def test_harmless_never_blocked_even_when_locked() -> None:
    gate.check_command("local.teacher->student.turnOnMute", risky=False)


def test_policy_persists(isolated_ecr_home: pathlib.Path) -> None:
    gate.set_dangerous_allowed(True)
    assert (isolated_ecr_home / "policy.json").exists()
    assert gate.dangerous_allowed()


def test_policy_can_be_locked_again() -> None:
    gate.set_dangerous_allowed(True)
    gate.set_dangerous_allowed(False)
    assert not gate.dangerous_allowed()
    with pytest.raises(gate.Blocked):
        gate.check_command("x.shutdown", risky=True)


def test_corrupt_policy_means_locked(isolated_ecr_home: pathlib.Path) -> None:
    (isolated_ecr_home / "policy.json").write_text("nope", encoding="utf-8")
    assert not gate.dangerous_allowed()


def test_check_command_accepts_explicit_override() -> None:
    """测试注入用，不该影响默认行为。"""
    gate.check_command("x.shutdown", risky=True, allowed=True)
    with pytest.raises(gate.Blocked):
        gate.check_command("x.shutdown", risky=True, allowed=False)
