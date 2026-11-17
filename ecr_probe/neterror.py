"""把 socket 层的错误翻译成人话。

Windows 上最常撞见的是 10054「主机中的一个程序关闭了一个已建立的连接」，
它只说了「断了」，没说「是谁断的、为什么断、接下来该试什么」。
把原始错误串直接甩到界面上，等于让用户自己翻译 Win32 错误码。

判定按异常类型走，不按错误文本走：同一件事在 Windows 上是
WinError 10054，在 macOS 上是 errno 104，文本还会随系统语言变。
"""

from __future__ import annotations

import socket


def _code(exc: OSError) -> str:
    """原始错误码。Windows 给 winerror，别的平台给 errno。"""
    win = getattr(exc, "winerror", None)
    if win:
        return f"WinError {win}"
    number = exc.errno
    return f"errno {number}" if number else ""


def describe(exc: BaseException) -> str:
    """一句话说明发生了什么，附上原始错误码。"""
    if isinstance(exc, TimeoutError):
        hint = "等待超时，对方没有回应"
    elif isinstance(exc, ConnectionResetError):
        hint = "对方主动断开了连接：这个端口可能不是 WebSocket 服务，或者请求的路径不对"
    elif isinstance(exc, BrokenPipeError):
        # 对方已经走了，这一侧再写就撞到断管。
        # 和复位是同一件事的两个阶段：先复位（读），再断管（写）。
        hint = "对方主动断开了连接：这个端口可能不是 WebSocket 服务，或者请求的路径不对"
    elif isinstance(exc, ConnectionRefusedError):
        hint = "这个端口没有服务在监听"
    elif isinstance(exc, ConnectionAbortedError):
        hint = "连接被中断"
    elif isinstance(exc, socket.gaierror):
        hint = "主机名解析失败"
    elif isinstance(exc, OSError):
        hint = f"系统错误：{exc.strerror or exc}"
    else:
        return str(exc)

    code = _code(exc) if isinstance(exc, OSError) else ""
    return f"{hint}（{code}）" if code else hint
