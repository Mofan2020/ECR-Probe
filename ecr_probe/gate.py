"""使用闸门与危险命令策略。

两层摩擦，故意分开：

1. **确认闸门**（gate）——首次使用要在界面里照抄一句确认语。挡的是
   "随手打开点两下"的人。会读文档的人当然抄得出来，它本来就不是安全
   机制，是让人先停一秒读完这段话。

2. **危险命令解锁**（policy）——关机、重启、网络管控、进程管控、远程
   键鼠、录屏这些，默认全部拒绝发送，必须在终端里跑一条命令才放开。
   挡的是"图形界面点错了"和"没意识到自己在干什么"。

真正的边界在本机地址守卫（guard.py），那两个才是硬的。这两层不是。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import pathlib
import time

# 要在界面里一字不差敲进来的确认语。
# 故意写得拗口：能抄完这句的人，至少知道自己在干什么。
CHALLENGE = "我确认只对自己拥有的设备使用，并已读完全部免责条款"

_GATE_VERSION = 1


def _root() -> pathlib.Path:
    return pathlib.Path.home() / ".ecr-probe"


def _gate_path() -> pathlib.Path:
    return _root() / "gate.json"


def _policy_path() -> pathlib.Path:
    return _root() / "policy.json"


def _digest(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ 确认闸门


def gate_passed() -> bool:
    """确认闸门过了没有。文件坏了就当没过。"""
    path = _gate_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return data.get("version") == _GATE_VERSION and data.get("digest") == _digest(CHALLENGE)


def pass_gate(text: str) -> bool:
    """校验用户敲的确认语。对了就落盘，返回 True。"""
    if _digest(text) != _digest(CHALLENGE):
        return False
    payload = {
        "version": _GATE_VERSION,
        "digest": _digest(CHALLENGE),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        _root().mkdir(parents=True, exist_ok=True)
        _gate_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        # 写不下去也不该让已经确认的人用不了
        pass
    return True


def reset_gate() -> None:
    with contextlib.suppress(OSError):
        _gate_path().unlink()


# ------------------------------------------------------------------ 危险命令


def _read_policy() -> dict:
    path = _policy_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def dangerous_allowed() -> bool:
    return bool(_read_policy().get("allow_dangerous"))


def set_dangerous_allowed(allowed: bool) -> None:
    payload = _read_policy()
    payload["allow_dangerous"] = bool(allowed)
    payload["changed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        _root().mkdir(parents=True, exist_ok=True)
        _policy_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


class Blocked(RuntimeError):
    """命令被策略拦住了。"""


def check_command(method: str, *, risky: bool, allowed: bool | None = None) -> None:
    """危险命令没解锁就抛 Blocked。allowed 用于测试注入。"""
    if not risky:
        return
    if allowed is None:
        allowed = dangerous_allowed()
    if allowed:
        return
    raise Blocked(
        f"{method} 属于危险命令，默认锁死。"
        "确认要用，先在终端执行：ecr-probe allow-dangerous"
    )
