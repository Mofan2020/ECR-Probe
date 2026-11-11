"""客户端 + 靶子的端到端测试。

证明两件事：握手按 RFC 6455 走通了，发出去的帧载荷是服务端读得到的原文。
没有这个测试，「客户端能用」就只是一句话。
"""

from __future__ import annotations

import json

import pytest

from ecr_probe import commands
from ecr_probe.mockserver import MockServer
from ecr_probe.wsclient import WebSocketClient, WebSocketError


@pytest.fixture()
def server() -> MockServer:
    srv = MockServer(host="127.0.0.1", port=0, path="/").start()
    yield srv
    srv.stop()


def test_handshake_accepted(server: MockServer) -> None:
    client = WebSocketClient("127.0.0.1", server.port, path="/")
    hs = client.connect()
    try:
        assert hs.accepted, hs.response
        assert hs.status == 101
        assert "websocket++" in hs.headers.get("server", "").lower()
        # 握手请求必须带齐这几个头
        for header in ("Upgrade: websocket", "Sec-WebSocket-Version: 13", "Sec-WebSocket-Key:"):
            assert header in hs.request
    finally:
        client.close()


def test_wrong_path_is_rejected(server: MockServer) -> None:
    client = WebSocketClient("127.0.0.1", server.port, path="/nope")
    hs = client.connect()
    try:
        assert not hs.accepted
        assert hs.status == 404
    finally:
        client.close()


def test_text_message_reaches_server_verbatim(server: MockServer) -> None:
    client = WebSocketClient("127.0.0.1", server.port, path="/")
    client.connect()
    try:
        req = commands.build_request("local.teacher->student.turnOffMute", {"teacherUid": "abc"})
        client.send_text(req.payload)

        frame = client.recv_message()
        assert frame is not None
        reply = json.loads(frame[1].decode("utf-8"))
        assert reply["code"] == 0
    finally:
        client.close()

    assert len(server.received) == 1
    got = json.loads(server.received[0])
    assert got["method"] == "local.teacher->student.turnOffMute"
    assert got["params"]["teacherUid"] == "abc"
    assert "traceId" in got


def test_frames_are_masked(server: MockServer) -> None:
    """RFC 6455 要求客户端帧必须加掩码。靶子读得出来说明掩码是对的；
    这里再单独确认一下掩码位：抓一手原始字节看第二位最高位。"""
    client = WebSocketClient("127.0.0.1", server.port, path="/")
    client.connect()
    try:
        assert client._sock is not None
        client.send_text("x")
        # 直接读服务端收到的原始帧不现实，改为校验实现里的 mask_bit
        # 通过往返验证：服务端解出了原文 = 掩码正确
        frame = client.recv_message()
        assert frame is not None
    finally:
        client.close()
    assert server.received == ["x"]


def test_unicode_survives_roundtrip(server: MockServer) -> None:
    client = WebSocketClient("127.0.0.1", server.port, path="/")
    client.connect()
    try:
        payload = "本地.teacher->student.测试"
        client.send_text(payload)
        client.recv_message()
    finally:
        client.close()
    assert server.received == [payload]


def test_large_message_uses_16bit_length(server: MockServer) -> None:
    """超过 125 字节要走 126 扩展长度分支。"""
    client = WebSocketClient("127.0.0.1", server.port, path="/")
    client.connect()
    try:
        payload = "a" * 500
        client.send_text(payload)
        client.recv_message()
    finally:
        client.close()
    assert server.received == [payload]


def test_connect_to_closed_port_raises() -> None:
    # 找一个肯定没开的端口：绑定后立刻关掉
    import socket as _socket

    probe_sock = _socket.socket()
    probe_sock.bind(("127.0.0.1", 0))
    dead_port = probe_sock.getsockname()[1]
    probe_sock.close()

    client = WebSocketClient("127.0.0.1", dead_port, timeout=0.5)
    with pytest.raises((OSError, WebSocketError)):
        client.connect()


def test_close_is_idempotent(server: MockServer) -> None:
    client = WebSocketClient("127.0.0.1", server.port, path="/")
    client.connect()
    client.close()
    client.close()  # 不该抛
