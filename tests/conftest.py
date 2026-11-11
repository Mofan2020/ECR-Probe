"""测试夹具：把所有落盘路径钉到临时目录。

铁律：测试绝不许碰用户真实的 ~/.ecr-probe。那里有闸门状态和危险命令
开关，测试跑一遍就把用户的设置冲掉是不可接受的。
"""

from __future__ import annotations

import pathlib

import pytest

from ecr_probe import gate


@pytest.fixture(autouse=True)
def isolated_ecr_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """把 gate 的根目录指到 tmp_path。

    gate 里所有路径都从 _root() 派生，钉住一个就全钉住了。
    不用 monkeypatch 环境变量，因为代码根本不读环境变量——
    读环境变量的隔离是假隔离，跑起来还是写真实目录。
    """
    root = tmp_path / ".ecr-probe"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gate, "_root", lambda: root)
    return root


@pytest.fixture()
def acked_gate(isolated_ecr_home: pathlib.Path) -> pathlib.Path:
    """过一个已经确认的闸门。需要发命令的测试用这个。"""
    assert gate.pass_gate(gate.CHALLENGE)
    assert gate.gate_passed()
    return isolated_ecr_home
