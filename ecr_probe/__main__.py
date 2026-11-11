"""入口。

默认开窗口。--cli 走命令行，方便脚本化验证。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import __version__, commands, guard, probe
from .bridge import Bridge

GUI_DIR = pathlib.Path(__file__).parent / "gui"

DISCLAIMER = """
ECR Probe —— 仅限本机使用
-------------------------
这个工具只连接本机地址（回环或本机网卡自己的地址），不会、也不应该
指向局域网里的其他设备。它用于研究自己机器上运行的客户端，做兼容性
验证和安全性自查。

用它去操作别人的设备是滥用行为，与作者无关。完整条款见 DISCLAIMER.md。
""".strip()


def cmd_locate(args: argparse.Namespace) -> int:
    try:
        result = probe.locate(args.host, args.ports)
    except (guard.NonLocalError, ValueError) as exc:
        print(f"拒绝或参数错误：{exc}", file=sys.stderr)
        return 2

    open_ports = [p.port for p in result.ports if p.open]
    print(f"扫了 {len(result.ports)} 个端口，开着 {len(open_ports)} 个")
    if open_ports:
        print(f"  开着：{open_ports}")

    if not result.hits:
        print("没有握手成功的路径。动态端口可能不在这个范围里。")
        return 1

    for hit in result.hits:
        mark = "OK " if hit.accepted else f"HTTP {hit.status}"
        fp = " [WebSocket++]" if hit.matched else ""
        print(f"  {hit.port} {hit.path}  {mark}{fp}  {hit.server or hit.error}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    try:
        addr, port = guard.guard_target(args.host, args.port)
    except (guard.NonLocalError, ValueError) as exc:
        print(f"拒绝：{exc}", file=sys.stderr)
        return 2

    params = {}
    if args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as exc:
            print(f"params 不是合法 JSON：{exc}", file=sys.stderr)
            return 2
        if not isinstance(params, dict):
            print("params 必须是 JSON 对象", file=sys.stderr)
            return 2

    req = commands.build_request(args.method, params)
    print(f"目标 ws://{addr}:{port}{args.path}")
    print(f"发出 {req.payload}")

    from .wsclient import WebSocketClient, WebSocketError

    client = WebSocketClient(addr, port, path=args.path, timeout=args.timeout)
    try:
        hs = client.connect()
    except (OSError, WebSocketError) as exc:
        print(f"连不上：{exc}", file=sys.stderr)
        return 1

    if not hs.accepted:
        print(f"握手被拒，HTTP {hs.status}", file=sys.stderr)
        print(hs.response, file=sys.stderr)
        return 1

    print("握手成功")
    client.send_text(req.payload)
    try:
        frame = client.recv_message()
    except (OSError, WebSocketError) as exc:
        print(f"没等到回应：{exc}")
        return 0
    finally:
        client.close()

    if frame is None:
        print("对端关闭了连接")
        return 0

    text = frame[1].decode("utf-8", errors="replace")
    print(f"回应 {len(text)} 字节：{text}")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from . import netlist
    from . import probe as probe_mod

    found = probe_mod.discover()
    total = len(netlist.listening_ports())
    print(f"本机共 {total} 个 TCP 监听")

    if not found:
        print("没认出易启学进程。用 locate 按候选端口扫。")
        return 1

    for item in found:
        who = item.process or f"pid {item.pid}"
        print(f"  {item.port}  {who}")
    print(f"\n命中 {len(found)} 个。下一步：ecr-probe locate --ports <上面的端口>")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for title, items in commands.GROUPS.items():
        print(f"\n{title}（{len(items)}）")
        for c in items:
            note = f"  # {c.note}" if c.note else ""
            print(f"  {c.name}{note}")
    print(f"\n合计 {len(commands.ALL_COMMANDS)} 条")
    print(commands.SPELLING_NOTE)
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    import webview

    print(DISCLAIMER)
    print()
    bridge = Bridge()
    webview.create_window(
        "ECR Probe",
        str(GUI_DIR / "index.html"),
        js_api=bridge,
        width=1180,
        height=820,
        min_size=(900, 640),
        text_select=True,
    )
    webview.start(debug=args.debug)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecr-probe",
        description="易启学课堂协议的本机探测台（静态分析产物，仅限本机地址）",
    )
    parser.add_argument("--version", action="version", version=f"ecr-probe {__version__}")
    sub = parser.add_subparsers(dest="command")

    gui = sub.add_parser("gui", help="开窗口（默认）")
    gui.add_argument("--debug", action="store_true", help="打开 webview 调试")
    gui.set_defaults(func=cmd_gui)

    lst = sub.add_parser("list", help="列出全部命令")
    lst.set_defaults(func=cmd_list)

    disc = sub.add_parser("discover", help="按进程名找本机监听端口")
    disc.set_defaults(func=cmd_discover)

    loc = sub.add_parser("locate", help="在本机找服务端")
    loc.add_argument("--host", default="127.0.0.1")
    loc.add_argument("--ports", default="33331,39173,3333", help='如 "1234" 或 "1000-1100"')
    loc.set_defaults(func=cmd_locate)

    snd = sub.add_parser("send", help="连上去发一条报文")
    snd.add_argument("--host", default="127.0.0.1")
    snd.add_argument("--port", type=int, required=True)
    snd.add_argument("--path", default="/")
    snd.add_argument("--method", required=True)
    snd.add_argument("--params", default="", help='JSON 对象，如 \'{"teacherUid":"x"}\'')
    snd.add_argument("--timeout", type=float, default=3.0)
    snd.set_defaults(func=cmd_send)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        args = parser.parse_args(["gui"] + (argv or []))

    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
