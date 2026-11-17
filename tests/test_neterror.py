"""错误翻译：界面上不能直接甩 Win32 原文。

Windows 上最常撞见的 10054「主机中的一个程序关闭了一个已建立的连接」，
原文既没说谁断的也没说下一步该试什么。用户看到的是这句话，不是诊断。
"""

from __future__ import annotations

import socket

from ecr_probe.neterror import describe


def test_connection_reset_is_explained() -> None:
    """对方主动断开——这是最常见的那个，得说清是谁断的。"""
    exc = ConnectionResetError(104, "主机中的一个程序关闭了一个已建立的连接")
    text = describe(exc)
    assert "对方主动断开" in text
    assert "不是 WebSocket 服务" in text
    assert "路径不对" in text


def test_connection_refused_says_nothing_is_listening() -> None:
    text = describe(ConnectionRefusedError(111, "Connection refused"))
    assert "没有服务在监听" in text
    assert "refused" not in text.lower()


def test_broken_pipe_says_peer_went_away() -> None:
    """对方已经走了，这一侧再写就撞到断管。

    和复位是同一件事的两个阶段：读的时候是复位，写的时候是断管。
    两种都得说人话，不能一个说人话一个甩英文。
    """
    text = describe(BrokenPipeError(32, "Broken pipe"))
    assert "对方主动断开" in text
    assert "Broken pipe" not in text


def test_timeout_is_explained() -> None:
    text = describe(TimeoutError("timed out"))
    assert "超时" in text


def test_gaierror_is_explained() -> None:
    text = describe(socket.gaierror(-2, "Name or service not known"))
    assert "解析" in text


def test_generic_oserror_keeps_system_text() -> None:
    """说不清原因的系统错误，原始文本得留着。"""
    text = describe(OSError(13, "Permission denied"))
    assert "Permission denied" in text


def test_error_code_is_kept_for_troubleshooting() -> None:
    """翻译成人话不等于丢掉原始错误码——排查还得靠它。"""
    text = describe(ConnectionResetError(104, "x"))
    assert "104" in text


def test_non_oserror_passes_through() -> None:
    """WebSocketError 这类自己写的异常，原样带过去。"""
    assert describe(RuntimeError("连接已关闭")) == "连接已关闭"
