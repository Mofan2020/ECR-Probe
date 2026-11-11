"""在本机找易启学的本地 WebSocket 服务。

为什么要"找"：本地 WS 端口是动态的。反编译 sub_691E30 能看到它调
getsockname 再 ntohs 返回端口，也就是 bind(0) 让内核分配，实际端口
由另一条通道告诉对端。所以没有固定端口可写。

找法分三步：
1. TCP 连一下，看端口开没开；
2. 开了就用 HTTP Upgrade 握手，试几个候选路径；
3. 看返回头里有没有 WebSocket++ 的指纹。

全程只连本机地址，靠 guard 拦。扫端口是串行的，不并发。
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

from . import guard, netlist
from .wsclient import Handshake, WebSocketClient, WebSocketError

# 候选路径。"/super_connect" 是从字符串表直接读到的；
# 其余是推断——本地服务端的 URI 路径没有确认过。
CANDIDATE_PATHS: tuple[str, ...] = (
    "/",
    "/super_connect",
    "/local",
    "/ws",
    "/local_ws",
    "/classroom",
    "/websocket",
)

# 被测端用 websocketpp，握手响应会带这个 Server 头
FINGERPRINT = "websocket++"


@dataclass
class PortProbe:
    port: int
    open: bool
    error: str = ""


@dataclass
class PathProbe:
    path: str
    status: int
    accepted: bool
    server: str = ""
    matched: bool = False
    handshake: Handshake | None = None
    error: str = ""
    port: int = 0


@dataclass
class LocateResult:
    host: str
    ports: list[PortProbe] = field(default_factory=list)
    hits: list[PathProbe] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.hits)


def parse_ports(spec: str) -> list[int]:
    """解析端口表达式："1234"、"1000-1100"、"1234,5678"、"1000-1010,2000"。

    上限 2000 个，防止手滑写成 1-65535 把本机扫穿。
    """
    ports: list[int] = []
    for chunk in spec.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            lo_s, _, hi_s = chunk.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError as exc:
                raise ValueError(f"端口段写错了: {chunk!r}") from exc
            if lo > hi:
                lo, hi = hi, lo
            ports.extend(range(lo, hi + 1))
        else:
            try:
                ports.append(int(chunk))
            except ValueError as exc:
                raise ValueError(f"端口写错了: {chunk!r}") from exc

    cleaned: list[int] = []
    for p in ports:
        guard.guard_port(p)
        if p not in cleaned:
            cleaned.append(p)

    if len(cleaned) > 2000:
        raise ValueError(f"一次最多扫 2000 个端口，你给了 {len(cleaned)} 个")
    if not cleaned:
        raise ValueError("没有有效端口")
    return cleaned


def tcp_open(host: str, port: int, timeout: float = 0.25) -> PortProbe:
    """TCP 连一次看端口开没开。仍然过 guard。"""
    try:
        addr, p = guard.guard_target(host, port)
    except guard.NonLocalError as exc:
        return PortProbe(port=port, open=False, error=str(exc))

    sock = socket.socket(socket.AF_INET6 if ":" in addr else socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((addr, p))
        return PortProbe(port=port, open=True)
    except OSError as exc:
        return PortProbe(port=port, open=False, error=str(exc))
    finally:
        sock.close()


def probe_paths(host: str, port: int, paths: tuple[str, ...] = CANDIDATE_PATHS,
                timeout: float = 2.0) -> list[PathProbe]:
    """对指定端口挨个试候选路径做 WS 握手。"""
    results: list[PathProbe] = []
    for path in paths:
        client = WebSocketClient(host, port, path=path, timeout=timeout)
        try:
            hs = client.connect()
        except (OSError, WebSocketError) as exc:
            results.append(PathProbe(path=path, status=0, accepted=False, port=port, error=str(exc)))
            continue
        finally:
            client.close()

        server = (hs.headers.get("server") or "") if hs.headers else ""
        results.append(
            PathProbe(
                path=path,
                status=hs.status,
                port=port,
                accepted=hs.accepted,
                server=server,
                matched=FINGERPRINT in server.lower(),
                handshake=hs,
            )
        )
    return results


def locate(host: str, port_spec: str, *, timeout: float = 0.25) -> LocateResult:
    """完整流程：扫端口 → 对开着的端口试路径。"""
    guard.guard_host(host)  # 先拦一次，非法目标不往下走
    ports = parse_ports(port_spec)
    result = LocateResult(host=host)

    for port in ports:
        probe = tcp_open(host, port, timeout=timeout)
        result.ports.append(probe)
        if not probe.open:
            continue
        hits = probe_paths(host, port, timeout=2.0)
        for hit in hits:
            # 只留握手成功的，或者带了 WebSocket++ 指纹的（说明服务在，但不收这个路径）
            if hit.accepted or hit.matched or hit.status in (400, 403, 426, 404):
                result.hits.append(hit)

    return result


# ---------------------------------------------------------------- 按进程找端口


@dataclass
class CandidatePort:
    """一个候选端口，附带它是怎么被找到的。"""

    port: int
    source: str          # "process" 精确命中 / "scan" 扫出来的
    process: str = ""
    pid: int = 0


def discover() -> list[CandidatePort]:
    """问内核要监听表，按进程名认出易启学的端口。

    这比盲扫准得多：不管端口是 33331 还是 51234，只要那个进程在监听
    就能拿到。拿不到时返回空列表，调用方退回去扫。
    """
    out: list[CandidatePort] = []
    for listener in netlist.target_ports():
        out.append(
            CandidatePort(
                port=listener.port,
                source="process",
                process=listener.process,
                pid=listener.pid,
            )
        )
    return out


def probe_candidates(host: str, ports: list[int], *, path: str = "") -> LocateResult:
    """对给定端口逐个握手，找到能连的那个路径。

    path 非空时只试这一个路径（精确模式）；
    否则按 CANDIDATE_PATHS 全试一遍（探索模式）。
    """
    guard.guard_host(host)
    result = LocateResult(host=host)
    paths = (path,) if path else CANDIDATE_PATHS

    for port in ports:
        guard.guard_port(port)
        opened = tcp_open(host, port)
        result.ports.append(opened)
        if not opened.open:
            continue
        for hit in probe_paths(host, port, paths=paths):
            # 握手成功的优先保留；其余状态码说明服务在但路径不对，也记下来
            if hit.accepted or hit.matched or hit.status in (400, 403, 426, 404, 426):
                result.hits.append(hit)

    # 握手成功的排前面，带 WebSocket++ 指纹的其次
    result.hits.sort(key=lambda h: (not h.accepted, not h.matched, h.port))
    return result
