"""找出本机哪些端口在监听、分别属于哪个进程。

为什么要这个：易启学的本地 WS 端口是动态分配的，没法写死。
盲扫几千个端口既慢又不准。正确做法是问内核「谁在监听」，
再按进程名认出它。

实现原则：走平台 API，不解析 netstat/lsof 的文本输出——
那些输出随系统语言和版本变，同一段正则只在一半机器上成立。
"""

from __future__ import annotations

import ctypes
import socket
import sys
from dataclasses import dataclass

# 进程名特征。小写匹配，命中一个就算。
PROCESS_HINTS: tuple[str, ...] = (
    "electronic-classroom",
    "seewo-ecr",
    "seewo-celink",
    "seewo",
)


@dataclass(frozen=True)
class Listener:
    """一个监听中的套接字。"""

    port: int
    pid: int
    process: str = ""
    exe: str = ""
    ipv6: bool = False

    @property
    def is_target(self) -> bool:
        haystack = f"{self.process} {self.exe}".lower()
        return any(hint in haystack for hint in PROCESS_HINTS)


# --------------------------------------------------------------- Windows


def _windows_listeners() -> list[Listener]:
    """GetExtendedTcpTable + TCP_TABLE_OWNER_PID_LISTENER。"""
    from ctypes import wintypes

    iphlpapi = ctypes.WinDLL("iphlpapi.dll")
    tcp_table_owner_pid_listener = 4

    af_inet = 2
    af_inet6 = 23

    out: list[Listener] = []

    class MIB_TCPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD),
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    class MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("ucLocalAddr", ctypes.c_ubyte * 16),
            ("dwLocalScopeId", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("ucRemoteAddr", ctypes.c_ubyte * 16),
            ("dwRemoteScopeId", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD),
            ("dwState", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    for af, row_type, ipv6 in (
        (af_inet, MIB_TCPROW_OWNER_PID, False),
        (af_inet6, MIB_TCP6ROW_OWNER_PID, True),
    ):
        size = wintypes.DWORD(0)
        iphlpapi.GetExtendedTcpTable(
            None, ctypes.byref(size), False, af, tcp_table_owner_pid_listener, 0
        )
        if not size.value:
            continue
        buf = ctypes.create_string_buffer(size.value)
        ret = iphlpapi.GetExtendedTcpTable(
            buf, ctypes.byref(size), False, af, tcp_table_owner_pid_listener, 0
        )
        if ret != 0:
            continue

        num = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
        array_type = row_type * num
        table = ctypes.cast(
            ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD),
            ctypes.POINTER(array_type),
        )[0]

        for row in table:
            raw_port = int(row.dwLocalPort)
            # dwLocalPort 是网络字节序，低 16 位才是端口
            port = ((raw_port & 0xFF) << 8) | ((raw_port >> 8) & 0xFF)
            if 0 < port < 65536:
                out.append(Listener(port=port, pid=int(row.dwOwningPid), ipv6=ipv6))

    return out


# --------------------------------------------------------------- 其它平台


def _psutil_listeners() -> list[Listener]:
    import psutil

    out: list[Listener] = []
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, PermissionError):
        # 没有权限看全表时退回到只看自己的进程，仍然比盲扫准
        conns = []
        try:
            for proc in psutil.process_iter(["pid"]):
                try:
                    conns.extend(proc.net_connections(kind="tcp"))
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
        except Exception:
            return out

    for conn in conns:
        if conn.status != psutil.CONN_LISTEN or conn.laddr is None:
            continue
        out.append(
            Listener(
                port=int(conn.laddr.port),
                pid=int(conn.pid or 0),
                ipv6=bool(conn.laddr.ip and ":" in str(conn.laddr.ip)),
            )
        )
    return out


# --------------------------------------------------------------- 对外


def annotate_processes(listeners: list[Listener]) -> list[Listener]:
    """给每条监听记录补上进程名。拿不到就留空，不影响端口本身。"""
    try:
        import psutil
    except ImportError:
        return listeners

    cache: dict[int, tuple[str, str]] = {}
    out: list[Listener] = []
    for item in listeners:
        if item.pid and item.pid not in cache:
            name, exe = "", ""
            try:
                proc = psutil.Process(item.pid)
                name = proc.name() or ""
                exe = proc.exe() or ""
            except Exception:
                pass
            cache[item.pid] = (name, exe)
        name, exe = cache.get(item.pid, ("", ""))
        out.append(
            Listener(
                port=item.port,
                pid=item.pid,
                process=name,
                exe=exe,
                ipv6=item.ipv6,
            )
        )
    return out


def listening_ports() -> list[Listener]:
    """本机全部 TCP 监听。失败返回空列表，调用方要能接受这个结果。"""
    try:
        if sys.platform == "win32":
            raw = _windows_listeners()
        else:
            raw = _psutil_listeners()
    except Exception:
        return []
    return annotate_processes(raw)


def target_ports() -> list[Listener]:
    """只留进程名像易启学的那些。"""
    seen: set[int] = set()
    out: list[Listener] = []
    for item in listening_ports():
        if item.is_target and item.port not in seen:
            seen.add(item.port)
            out.append(item)
    return out


def loopback_only(listeners: list[Listener]) -> list[Listener]:
    """按端口去重，保留一条。"""
    seen: set[int] = set()
    out: list[Listener] = []
    for item in listeners:
        if item.port in seen:
            continue
        seen.add(item.port)
        out.append(item)
    return out


def is_ipv6_available() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM):
            return True
    except OSError:
        return False
