"""写系统剪贴板。

界面要能一键复制日志、报文、错误信息。JS 的 navigator.clipboard 在
file:// 下不算安全上下文，可能被拒，所以主路径走 Python 侧，JS 只做兜底。

平台差异用各自的原生接口：
- Windows: user32 的剪贴板 API（ctypes）
- macOS: pbcopy（系统自带，行为稳定）
- Linux: wl-copy / xclip / xsel，有哪个用哪个
"""

from __future__ import annotations

import shutil
import subprocess
import sys


class ClipboardError(RuntimeError):
    pass


def _windows_copy(text: str) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    if not user32.OpenClipboard(None):
        raise ClipboardError("OpenClipboard 失败")
    handle = None
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise ClipboardError("GlobalAlloc 失败")
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            raise ClipboardError("GlobalLock 失败")
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise ClipboardError("SetClipboardData 失败")
        handle = None  # 交给系统了，不要再 free
    finally:
        if handle:
            kernel32.GlobalFree(handle)
        user32.CloseClipboard()


def _pipe_copy(cmd: list[str], text: str) -> None:
    proc = subprocess.run(cmd, input=text.encode("utf-8"), capture_output=True, timeout=5)
    if proc.returncode != 0:
        raise ClipboardError(proc.stderr.decode("utf-8", errors="replace").strip() or "复制失败")


def copy_text(text: str) -> None:
    """把文本写进系统剪贴板。失败抛 ClipboardError。"""
    if not isinstance(text, str):
        raise ClipboardError("只能复制文本")

    if sys.platform == "win32":
        _windows_copy(text)
        return

    if sys.platform == "darwin":
        _pipe_copy(["pbcopy"], text)
        return

    for candidate in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if shutil.which(candidate[0]):
            _pipe_copy(candidate, text)
            return

    raise ClipboardError("这个平台上找不到可用的剪贴板工具")
