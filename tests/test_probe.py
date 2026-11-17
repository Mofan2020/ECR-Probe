"""探测层的测试：端口表达式、TCP 探测、路径握手、指纹识别。"""

from __future__ import annotations

import socket
import struct

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


# ------------------------------------------------------------------ 对端突然断开


class _ResetServer:
    """接受连接后立刻用 RST 断开。

    真实被测端不认路径时经常这么干：TCP 握手成了，然后直接复位。
    SO_LINGER 设成 0，close() 就发 RST 而不是 FIN——只有 RST 才会让
    对面 recv() 抛 ConnectionResetError。
    """

    def __init__(self) -> None:
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.port = self._sock.getsockname()[1]
        self._stop = False

    def __enter__(self) -> _ResetServer:
        import threading

        def serve() -> None:
            while not self._stop:
                try:
                    conn, _ = self._sock.accept()
                except OSError:
                    return
                conn.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_LINGER,
                    struct.pack("ii", 1, 0),
                )
                conn.close()

        threading.Thread(target=serve, daemon=True).start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._stop = True
        self._sock.close()


def test_probe_paths_reports_reset_as_readable_text() -> None:
    """对方复位时，界面上不能出现 Win32 原文。"""
    with _ResetServer() as srv:
        results = probe.probe_paths("127.0.0.1", srv.port, paths=("/",), timeout=2.0)

    error = results[0].error
    assert error, "复位也算一种失败，得有说法"
    assert "对方主动断开" in error
    assert "已建立的连接" not in error, "还在甩 Windows 原文"


def test_tcp_open_error_is_readable() -> None:
    """连不上的端口，错误文本也得是人话。"""
    with _ResetServer() as srv:
        # 端口开着但立刻复位，TCP 层往往也报连接被重置
        result = probe.tcp_open("127.0.0.1", srv.port)
    if not result.open and result.error:
        assert "WinError" not in result.error
        assert "errno" not in result.error or "104" in result.error
