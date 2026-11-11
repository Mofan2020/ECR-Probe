"""输出编码的测试。

Windows 控制台默认代码页编码不了中文，一 print 就崩。这个在 CI 的
windows-latest 上真实发生过（`ecr-probe resources` 打印「持久目录：」）。
这里用假流把那个场景钉住——不能只在 Mac 上跑绿就算数。
"""

from __future__ import annotations

import sys

import pytest

from ecr_probe import __main__ as cli


class BoomStream:
    """模拟 Windows 控制台：写中文就抛 UnicodeEncodeError。"""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> int:
        CANT_ENCODE = "charmap"
        if any("\u4e00" <= ch <= "\u9fff" for ch in text):
            raise UnicodeEncodeError(CANT_ENCODE, text, 0, len(text), "character maps to <undefined>")
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        pass


class ReconfigurableStream(BoomStream):
    """支持 reconfigure 的假流，记录被要求改成什么。"""

    def __init__(self) -> None:
        super().__init__()
        self.encoding = "cp1252"
        self.errors = "strict"

    def reconfigure(self, *, encoding: str | None = None, errors: str | None = None) -> None:
        if encoding is not None:
            self.encoding = encoding
        if errors is not None:
            self.errors = errors


class NoReconfigure:
    """不支持 reconfigure 的对象——不能因此崩掉。"""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass


def test_reconfigures_stdout_and_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = ReconfigurableStream(), ReconfigurableStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    cli._ensure_output_encoding()

    assert out.encoding == "utf-8"
    assert out.errors == "replace", "必须带 replace，否则仍然会抛异常"
    assert err.encoding == "utf-8"


def test_survives_stream_without_reconfigure(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 reconfigure 的流（比如被重定向成奇怪对象）不该让程序崩。"""
    monkeypatch.setattr(sys, "stdout", NoReconfigure())
    monkeypatch.setattr(sys, "stderr", NoReconfigure())
    cli._ensure_output_encoding()  # 不抛就算过


def test_chinese_survives_after_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """修完之后写中文要真的能过去。"""
    out = ReconfigurableStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", ReconfigurableStream())

    cli._ensure_output_encoding()
    # 假流只要 encoding 变成 utf-8 就不再抛
    assert out.encoding == "utf-8"


def test_main_calls_encoding_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() 必须调它，否则打包后照挂。"""
    called: list[int] = []
    monkeypatch.setattr(cli, "_ensure_output_encoding", lambda: called.append(1))

    with pytest.raises(SystemExit):
        cli.main(["--version"])

    assert called == [1], "main() 没有先修输出编码"
