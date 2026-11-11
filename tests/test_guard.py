"""守卫测试。这是整个工具的道德边界，必须证明它真的会拦。

不只测「该拒的拒了」，也要测「该放的放行」——只会拒绝的守卫等于把
功能也一起关掉了，那是另一种失败。
"""

from __future__ import annotations

import socket

import pytest

from ecr_probe import guard

# --------------------------------------------------------------------- 拒绝


@pytest.mark.parametrize(
    "addr",
    [
        "8.8.8.8",
        "1.1.1.1",
        "192.168.1.32",       # 用户自己的另一台机器，也必须是「别人」
        "10.0.0.1",
        "172.16.0.5",
        "203.0.113.7",
        "2001:4860:4860::8888",
        "0.0.0.0",
        "::",
        "*",
        "",
    ],
)
def test_public_and_wildcard_addresses_are_refused(addr: str) -> None:
    with pytest.raises(guard.NonLocalError):
        guard.resolve_local(addr, allowed={"127.0.0.1", "::1"})


def test_lan_address_refused_when_not_on_this_machine() -> None:
    """192.168.x 是不是本机，取决于它有没有挂在本机网卡上。没有就拒。"""
    with pytest.raises(guard.NonLocalError):
        guard.resolve_local("192.168.1.32", allowed={"127.0.0.1"})


def test_hostname_resolving_outside_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS 重绑定：名字看着无害，解析出来是外网地址。必须拒。"""
    fake = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: fake)
    with pytest.raises(guard.NonLocalError):
        guard.resolve_local("innocent.example", allowed={"127.0.0.1"})


def test_hostname_mixing_local_and_remote_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """只要有一条解析结果不是本机就拒——不做「至少一个本机」的宽松判断。"""
    fake = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: fake)
    with pytest.raises(guard.NonLocalError):
        guard.resolve_local("mixed.example", allowed={"127.0.0.1"})


@pytest.mark.parametrize("addr", ["*", "0.0.0.0", "::", ""])
def test_wildcard_reports_specific_reason(addr: str) -> None:
    """通配地址要给出「通配」这个原因，不是笼统的「不是本机」。

    这条分支在行为上是冗余的（后面两道判断也会拒），
    但它存在的意义是错误信息要能让人看懂，所以单独钉住文案。
    """
    with pytest.raises(guard.NonLocalError, match="通配地址"):
        guard.resolve_local(addr, allowed={"127.0.0.1"})


def test_non_string_host_refused() -> None:
    with pytest.raises(guard.NonLocalError):
        guard.resolve_local(127001)  # type: ignore[arg-type]


# --------------------------------------------------------------------- 放行


@pytest.mark.parametrize("addr", ["127.0.0.1", "127.0.0.2", "127.255.255.254", "::1"])
def test_loopback_is_allowed(addr: str) -> None:
    assert guard.resolve_local(addr, allowed={"127.0.0.1", "::1"}) == [
        guard._normalize(addr)
    ]


def test_ipv4_mapped_loopback_is_allowed() -> None:
    assert guard.resolve_local("::ffff:127.0.0.1", allowed={"127.0.0.1"}) == ["127.0.0.1"]


def test_local_interface_address_is_allowed() -> None:
    """本机网卡自己的地址要放行——被测端可能只绑网卡不绑回环。"""
    assert guard.resolve_local("192.168.9.9", allowed={"192.168.9.9"}) == ["192.168.9.9"]


def test_bracketed_ipv6_is_handled() -> None:
    assert guard.resolve_local("[::1]", allowed={"::1"}) == ["::1"]


def test_localhost_name_resolves_to_loopback() -> None:
    addrs = guard.resolve_local("localhost", allowed={"127.0.0.1", "::1"})
    assert all(guard.is_loopback(a) for a in addrs)


# --------------------------------------------------------------------- 端口


@pytest.mark.parametrize("port", [0, -1, 65536, 999999])
def test_bad_ports_refused(port: int) -> None:
    with pytest.raises(guard.NonLocalError):
        guard.guard_port(port)


def test_bool_port_refused() -> None:
    with pytest.raises(guard.NonLocalError):
        guard.guard_port(True)  # type: ignore[arg-type]


def test_good_port_passes() -> None:
    assert guard.guard_port(18080) == 18080


# --------------------------------------------------------------------- 合流


def test_guard_target_returns_loopback_first() -> None:
    addr, port = guard.guard_target("localhost", 18080)
    assert guard.is_loopback(addr)
    assert port == 18080


def test_guard_target_refuses_remote() -> None:
    with pytest.raises(guard.NonLocalError):
        guard.guard_target("8.8.8.8", 443)
