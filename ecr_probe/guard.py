"""本机地址强制守卫。

这是整个工具的道德边界。任何出站连接都必须先过这里。
测试覆盖它，别绕过，别加「允许任意局域网」的开关。

允许的目标只有两类：
1. 回环地址（127.0.0.0/8、::1）——本机自己；
2. 本机网卡上真实存在的地址——还是本机自己，只是换了个 IP 表述。

其余一律拒绝。被测端可能只绑网卡 IP 不绑回环，所以第 2 类必须有；
但它跟"局域网内任意主机"是两件事，别混。

设计意图：这个工具用来研究自己机器上的客户端，不是用来打别人。
把限制写死在代码里，而不是写在文档里求人自觉。
"""

from __future__ import annotations

import ipaddress
import socket

_LOOPBACK_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)

# 能"连上"但语义上指向所有主机的地址，明确拒绝
_WILDCARD = frozenset({"*", "0.0.0.0", "::", "0:0:0:0:0:0:0:0", ""})


class NonLocalError(RuntimeError):
    """目标不是本机地址，或者无法证明它是本机地址。"""


# ---------------------------------------------------------------- 本地地址枚举


def local_addresses() -> set[str]:
    """枚举本机所有网卡地址。

    用 psutil（平台 API）而不是解析 ifconfig/ipconfig 输出——
    文本输出随系统语言变化，靠不住。

    拿不到 psutil 时退回两个标准库办法，宁可少认几个地址也不能放宽判断。
    """
    addrs: set[str] = {"127.0.0.1", "::1"}

    try:
        import psutil

        for name, entries in psutil.net_if_addrs().items():
            if name.lower().startswith("lo"):
                continue
            for entry in entries:
                if entry.family in (socket.AF_INET, socket.AF_INET6):
                    addr = str(entry.address).split("%", 1)[0]
                    try:
                        ipaddress.ip_address(addr)
                    except ValueError:
                        continue
                    addrs.add(addr)
        return addrs
    except ImportError:
        pass

    # 退路一：主机名解析出来的地址
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = str(info[4][0]).split("%", 1)[0]
            try:
                ipaddress.ip_address(addr)
            except ValueError:
                continue
            addrs.add(addr)
    except (socket.gaierror, OSError):
        pass

    # 退路二：UDP connect 不会发任何包，只是让内核选一条出口路由
    for probe in ("8.8.8.8", "1.1.1.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 53))
                addrs.add(sock.getsockname()[0])
        except OSError:
            continue

    return addrs


# ---------------------------------------------------------------- 判断


def _strip_brackets(host: str) -> str:
    host = host.strip()
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def _normalize(addr: str) -> str:
    """把地址规范化，IPv4 映射地址折回 IPv4。"""
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


def is_loopback(addr: str) -> bool:
    ip = ipaddress.ip_address(_normalize(addr))
    return any(ip in net for net in _LOOPBACK_NETS)


def is_local(addr: str, *, allowed: set[str] | None = None) -> bool:
    """地址是不是本机的。allowed 用于测试注入。"""
    try:
        norm = _normalize(addr)
    except ValueError:
        return False
    if is_loopback(norm):
        return True
    pool = local_addresses() if allowed is None else allowed
    return norm in pool


def resolve_local(host: str, *, allowed: set[str] | None = None) -> list[str]:
    """解析 host，要求**全部**解析结果都是本机地址。

    返回规范化后的地址列表。任何一条不属于本机就抛 NonLocalError——
    不做「至少有一个是本机」的宽松判断，那等于给 DNS 重绑定留门。
    """
    if not isinstance(host, str):
        raise NonLocalError(f"主机名必须是字符串，收到 {type(host).__name__}")

    host = _strip_brackets(host)
    if host in _WILDCARD:
        raise NonLocalError(f"{host!r} 是通配地址，拒绝")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None

    if literal is not None:
        norm = _normalize(host)
        if not is_local(norm, allowed=allowed):
            raise NonLocalError(f"{host} 不是本机地址")
        return [norm]

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise NonLocalError(f"解析 {host} 失败: {exc}") from exc

    addrs: list[str] = []
    for info in infos:
        norm = _normalize(str(info[4][0]))
        if norm not in addrs:
            addrs.append(norm)

    if not addrs:
        raise NonLocalError(f"{host} 没有任何解析结果")

    bad = [a for a in addrs if not is_local(a, allowed=allowed)]
    if bad:
        raise NonLocalError(f"{host} 解析到非本机地址 {bad}，拒绝")

    return addrs


def guard_host(host: str, *, allowed: set[str] | None = None) -> str:
    """校验并返回一个可以直接连接的本机地址。"""
    addrs = resolve_local(host, allowed=allowed)
    # 回环优先：同样能用时优先走回环，少一次网卡往返
    addrs.sort(key=lambda a: (not is_loopback(a), ":" in a, a))
    return addrs[0]


def guard_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int):
        raise NonLocalError(f"端口必须是整数，收到 {type(port).__name__}")
    if not 1 <= port <= 65535:
        raise NonLocalError(f"端口 {port} 越界")
    return port


def guard_target(host: str, port: int, *, allowed: set[str] | None = None) -> tuple[str, int]:
    """连接前统一入口。所有出站连接都必须调这个。"""
    return guard_host(host, allowed=allowed), guard_port(port)
