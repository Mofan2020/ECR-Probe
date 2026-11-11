"""把 Node 那边的 app.js 冒烟测试接进 pytest。

这样跑 `pytest` 一个命令就同时覆盖 Python 和前端，不用记两套入口。
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

BASE = pathlib.Path(__file__).parent.parent
SCRIPT = BASE / "scripts" / "check-app-js.js"


def test_script_exists() -> None:
    assert SCRIPT.exists(), "冒烟脚本不见了"


@pytest.mark.skipif(shutil.which("node") is None, reason="本机没有 node")
def test_app_js_boots_in_fake_dom() -> None:
    proc = subprocess.run(
        ["node", str(SCRIPT)],
        capture_output=True,
        text=True,
        # 必须显式指定编码：Windows 上 text=True 会按本地代码页（cp1252/cp936）
        # 解码，而 node 输出的是 UTF-8 中文，直接 UnicodeDecodeError，
        # stdout 变成 None，后面 "x" in None 又炸一次。
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=BASE,
    )
    assert proc.returncode == 0, f"app.js 冒烟测试失败：\n{proc.stdout}\n{proc.stderr}"
    assert "全部通过" in proc.stdout
