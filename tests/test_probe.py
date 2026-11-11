"""探测层的测试：端口表达式、TCP 探测、路径握手、指纹识别。"""

from __future__ import annotations

import socket

import pytest

from ecr_probe import guard, probe
from ecr_probe.mockserver import MockServer

# ------------------------------------------------------------------ 端口表达式


def test_parse_single() -> None:
    assert probe.parse_ports("18080") == [18080]


def test_parse_range() -> None:
    assert probe.parse_ports("1000-1003") == [1000, 1001, 1002, 1003]


def test_parse_reversed_range_is_normalized() -> None:
    assert probe.parse_ports("1003-1000") == [1000, 1001, 1002, 1003]


def test_parse_mixed_and_dedup() -> None:
    assert probe.parse_ports("1000-1001,1001,2000") == [1000, 1001, 2000]


def test_parse_rejects_garbage() -> None:
    for bad in ("abc", "1000-abc", "", ",,"):
        with pytest.raises(ValueError):
            probe.parse_ports(bad)


def test_parse_rejects_out_of_range() -> None:
    with pytest.raises(guard.NonLocalError):
        probe.parse_ports("70000")


def test_parse_refuses_huge_scan() -> None:
    """防止手滑写成 1-65535 把本机扫穿。"""
    with pytest.raises(ValueError, match="2000"):
        probe.parse_ports("1-65535")


# ------------------------------------------------------------------ TCP 探测


def _dead_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_tcp_open_detects_listener() -> None:
    with MockServer(host="127.0.0.1", port=0) as srv:
        assert probe.tcp_open("127.0.0.1", srv.port).open


def test_tcp_open_detects_closed() -> None:
    assert not probe.tcp_open("127.0.0.1", _dead_port()).open


def test_tcp_open_refuses_remote() -> None:
    result = probe.tcp_open("8.8.8.8", 53)
    assert not result.open
    assert "不是本机地址" in result.error


# ------------------------------------------------------------------ 路径握手


def test_probe_paths_finds_correct_one() -> None:
    with MockServer(host="127.0.0.1", port=0, path="/") as srv:
        results = probe.probe_paths("127.0.0.1", srv.port, paths=("/x", "/"))
    by_path = {r.path: r for r in results}
    assert not by_path["/x"].accepted
    assert by_path["/"].accepted
    assert by_path["/"].matched, "应该认出 WebSocket++ 指纹"
    assert by_path["/"].port == srv.port


def test_probe_paths_finds_super_connect() -> None:
    """/super_connect 是从字符串表直接读到的路径，必须能握上。"""
    with MockServer(host="127.0.0.1", port=0, path="/super_connect") as srv:
        results = probe.probe_paths("127.0.0.1", srv.port, paths=("/super_connect",))
    assert results[0].accepted
    assert results[0].matched


def test_probe_paths_reports_error_when_closed() -> None:
    results = probe.probe_paths("127.0.0.1", _dead_port(), paths=("/",), timeout=0.5)
    assert not results[0].accepted
    assert results[0].error


# ------------------------------------------------------------------ 完整流程


def test_locate_finds_service_on_scan() -> None:
    with MockServer(host="127.0.0.1", port=0, path="/") as srv:
        result = probe.locate("127.0.0.1", str(srv.port))
    assert result.found
    assert any(h.accepted for h in result.hits)


def test_locate_returns_nothing_for_dead_ports() -> None:
    result = probe.locate("127.0.0.1", str(_dead_port()))
    assert not result.found
    assert not any(p.open for p in result.ports)


def test_locate_refuses_remote_host() -> None:
    with pytest.raises(guard.NonLocalError):
        probe.locate("8.8.8.8", "53")


def test_locate_does_not_scan_when_guard_rejects() -> None:
    """守卫拒绝时必须在开扫之前就停，不能扫完再拒。"""
    with pytest.raises(guard.NonLocalError):
        probe.locate("203.0.113.9", "80-90")
