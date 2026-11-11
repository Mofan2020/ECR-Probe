"""最小的 WebSocket 服务端，只用于测试和离线演练。

它冒充被测端的握手行为：算 Sec-WebSocket-Accept、回 101、
按 RFC 6455 收客户端帧、把收到的每条消息记下来。

这不是被测端，它不会做任何课堂相关的事。它的作用只有一个：
证明客户端发出去的字节是对的方向、对的形状。

也可以单独跑，当回环靶子用：

    python -m ecr_probe.mockserver --port 18080 --path /
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import socket
import struct
import threading
from dataclasses import dataclass, field

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return bytes(buf)


def _read_frame(sock: socket.socket) -> tuple[int, bytes] | None:
    try:
        b0, b1 = _recv_exact(sock, 2)
    except ConnectionError:
        return None
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _send_frame(sock: socket.socket, opcode: int, payload: bytes) -> None:
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < 65536:
        header.append(126)
        header += struct.pack("!H", length)
    else:
        header.append(127)
        header += struct.pack("!Q", length)
    sock.sendall(bytes(header) + payload)


@dataclass
class MockServer:
    """线程里跑的 WS 靶子。"""

    host: str = "127.0.0.1"
    port: int = 0
    path: str = "/"
    echo: bool = True
    received: list[str] = field(default_factory=list)
    handshakes: list[str] = field(default_factory=list)
    _sock: socket.socket | None = None
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _clients: list[socket.socket] = field(default_factory=list)

    # ---------- 生命周期 ----------

    def start(self) -> MockServer:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(8)
        sock.settimeout(0.2)
        self.port = sock.getsockname()[1]
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        for client in list(self._clients):
            with contextlib.suppress(OSError):
                client.close()
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def __enter__(self) -> MockServer:
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # ---------- 主循环 ----------

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            self._clients.append(client)
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(5.0)
        try:
            raw = bytearray()
            while b"\r\n\r\n" not in raw:
                chunk = client.recv(4096)
                if not chunk:
                    return
                raw += chunk
            text = raw.decode("iso-8859-1", errors="replace")
            head = text.split("\r\n\r\n", 1)[0]
            self.handshakes.append(head)

            lines = head.split("\r\n")
            request_line = lines[0]
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, _, v = line.partition(":")
                    headers[k.strip().lower()] = v.strip()

            request_path = request_line.split(" ")[1] if " " in request_line else ""

            if "sec-websocket-key" not in headers:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return

            if self.path and request_path != self.path:
                body = b"wrong path"
                client.sendall(
                    b"HTTP/1.1 404 Not Found\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                return

            accept = _accept_key(headers["sec-websocket-key"])
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "Server: WebSocket++/0.8.2\r\n"
                "\r\n"
            )
            client.sendall(response.encode("ascii"))

            while not self._stop.is_set():
                frame = _read_frame(client)
                if frame is None:
                    return
                opcode, payload = frame
                if opcode == 0x8:
                    _send_frame(client, 0x8, payload[:2])
                    return
                if opcode == 0x9:
                    _send_frame(client, 0xA, payload)
                    continue
                if opcode in (0x1, 0x2):
                    text_payload = payload.decode("utf-8", errors="replace")
                    self.received.append(text_payload)
                    if self.echo:
                        try:
                            req = json.loads(text_payload)
                        except json.JSONDecodeError:
                            reply = {"code": 0, "message": "ok", "data": {"echo": text_payload}}
                        else:
                            reply = {
                                "code": 0,
                                "message": "ok",
                                "data": {"method": req.get("method"), "echo": True},
                            }
                        _send_frame(client, 0x1, json.dumps(reply).encode("utf-8"))
        except (OSError, ConnectionError):
            return
        finally:
            with contextlib.suppress(OSError):
                client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="回环 WebSocket 靶子，仅用于测试")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--path", default="/")
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print(f"拒绝在 {args.host} 上监听：靶子只许绑回环地址")
        return 2

    server = MockServer(host=args.host, port=args.port, path=args.path).start()
    print(f"靶子在 ws://{args.host}:{server.port}{args.path} 上，Ctrl-C 退出")
    import time

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
