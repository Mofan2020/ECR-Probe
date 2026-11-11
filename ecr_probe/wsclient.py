"""最小可用的 RFC 6455 WebSocket 客户端。

自己实现而不是引三方库，理由有二：
1. 探测工具要能看见发出去的每个字节，包括握手头；
2. 被测端的握手行为本身可能就是线索（它认哪些路径、返回什么状态码）。

只实现客户端需要的那部分：握手、文本/二进制帧、ping/pong、close。
不实现压缩扩展、不做流式分片写出。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
from dataclasses import dataclass, field

_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(RuntimeError):
    """握手或帧层面的错误。"""


@dataclass
class Handshake:
    """握手过程留档，界面上要显示。"""

    request: str
    response: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.status == 101


def _read_until(sock: socket.socket, marker: bytes, limit: int = 65536) -> bytes:
    buf = bytearray()
    while marker not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketError(f"连接在读到 {marker!r} 前关闭（已读 {len(buf)} 字节）")
        buf += chunk
        if len(buf) > limit:
            raise WebSocketError(f"响应超过 {limit} 字节仍未见 {marker!r}")
    return bytes(buf)


def _parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in raw.split("\r\n")[1:]:
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return headers


class WebSocketClient:
    """同步客户端。GUI 里放线程里跑，别在主线程阻塞。"""

    def __init__(self, host: str, port: int, path: str = "/", timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.path = path if path.startswith("/") else "/" + path
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self.handshake: Handshake | None = None
        self._recv_buf = bytearray()

    # ---------- 握手 ----------

    def connect(self) -> Handshake:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        host_header = f"[{self.host}]" if ":" in self.host else self.host
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {host_header}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )

        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._sock = sock
        sock.sendall(request.encode("ascii"))

        raw = _read_until(sock, b"\r\n\r\n")
        text = raw.decode("iso-8859-1", errors="replace")
        head = text.split("\r\n\r\n", 1)[0]

        status = 0
        status_line = head.split("\r\n", 1)[0]
        parts = status_line.split(None, 2)
        if len(parts) >= 2 and parts[1].isdigit():
            status = int(parts[1])

        headers = _parse_headers(head)
        self.handshake = Handshake(request=request, response=head, status=status, headers=headers)

        if status != 101:
            self.close()
            return self.handshake

        # 校验 Accept，服务端算错说明不是标准实现
        expected = base64.b64encode(
            hashlib.sha1((key + _GUID).encode("ascii")).digest()
        ).decode("ascii")
        got = headers.get("sec-websocket-accept", "")
        if got and got != expected:
            self.close()
            raise WebSocketError(f"Sec-WebSocket-Accept 不匹配：期望 {expected}，收到 {got}")

        # 握手响应可能已经带了帧，塞回缓冲
        leftover = raw[len(head) + 4 :]
        if leftover:
            self._recv_buf += leftover

        return self.handshake

    @property
    def connected(self) -> bool:
        return self._sock is not None and self.handshake is not None and self.handshake.accepted

    # ---------- 发送 ----------

    def send_text(self, text: str) -> int:
        return self._send_frame(_OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> int:
        return self._send_frame(_OP_BINARY, data)

    def _send_frame(self, opcode: int, payload: bytes) -> int:
        if self._sock is None:
            raise WebSocketError("未连接")

        header = bytearray()
        header.append(0x80 | opcode)  # FIN + opcode

        length = len(payload)
        mask_bit = 0x80  # 客户端必须加掩码
        if length < 126:
            header.append(mask_bit | length)
        elif length < 65536:
            header.append(mask_bit | 126)
            header += struct.pack("!H", length)
        else:
            header.append(mask_bit | 127)
            header += struct.pack("!Q", length)

        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

        frame = bytes(header) + masked
        self._sock.sendall(frame)
        return len(frame)

    # ---------- 接收 ----------

    def _read_exact(self, n: int) -> bytes:
        while len(self._recv_buf) < n:
            chunk = self._sock.recv(65536) if self._sock else b""
            if not chunk:
                raise WebSocketError("连接已关闭")
            self._recv_buf += chunk
        out = bytes(self._recv_buf[:n])
        del self._recv_buf[:n]
        return out

    def recv_message(self) -> tuple[int, bytes] | None:
        """收一条完整消息。返回 (opcode, payload)；收到 close 返回 None。

        分片会被拼起来；ping 自动回 pong 后继续等下一条。
        """
        fragments = bytearray()
        first_opcode: int | None = None

        while True:
            b0, b1 = self._read_exact(2)
            fin = bool(b0 & 0x80)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F

            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]

            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

            if opcode == _OP_CLOSE:
                return None
            if opcode == _OP_PING:
                self._send_frame(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue

            if opcode == _OP_CONT:
                fragments += payload
            else:
                first_opcode = opcode
                fragments = bytearray(payload)

            if fin:
                return (first_opcode if first_opcode is not None else _OP_TEXT, bytes(fragments))

    # ---------- 收尾 ----------

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            if self.handshake is not None and self.handshake.accepted:
                self._send_frame(_OP_CLOSE, struct.pack("!H", 1000))
        except (OSError, WebSocketError):
            pass
        try:
            self._sock.close()
        finally:
            self._sock = None

    def __enter__(self) -> WebSocketClient:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
