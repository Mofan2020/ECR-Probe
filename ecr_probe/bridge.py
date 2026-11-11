"""pywebview 的 Python 侧。

JS 里调 window.pywebview.api.xxx()，这里返回 dict，界面直接显示。
所有出站连接都经过 guard，桥本身不再放宽任何限制。
"""

from __future__ import annotations

import json
import threading
import time

from . import clipboard, commands, guard, netlist, probe
from .rules import RuleStore
from .wsclient import WebSocketClient, WebSocketError


class Bridge:
    def __init__(self, rule_path=None) -> None:
        self._client: WebSocketClient | None = None
        self._lock = threading.Lock()
        self._logs: list[str] = []
        self._target: tuple[str, int, str] | None = None
        self.rules = RuleStore(rule_path)

    # ---------------------------------------------------------------- 日志

    def _log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        for line in str(text).splitlines() or [""]:
            self._logs.append(f"[{stamp}] {line}")
        if len(self._logs) > 2000:
            del self._logs[:500]

    def get_log(self) -> dict:
        return {"ok": True, "lines": list(self._logs)}

    def clear_log(self) -> dict:
        self._logs.clear()
        return {"ok": True}

    # ---------------------------------------------------------------- 命令目录

    def get_commands(self) -> dict:
        groups = []
        for title, items in commands.GROUPS.items():
            groups.append(
                {
                    "title": title,
                    "items": [
                        {
                            "name": c.name,
                            "direction": c.direction,
                            "note": c.note,
                            "params": c.params_hint,
                        }
                        for c in items
                    ],
                }
            )
        return {
            "ok": True,
            "groups": groups,
            "total": len(commands.ALL_COMMANDS),
            "spelling": commands.SPELLING_NOTE,
        }

    # ---------------------------------------------------------------- 环境

    def local_info(self) -> dict:
        try:
            addrs = sorted(guard.local_addresses())
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "addresses": addrs}

    def discover(self) -> dict:
        """按进程名找本机在监听的端口。"""
        try:
            found = probe.discover()
        except Exception as exc:
            self._log(f"读监听表失败：{exc}")
            return {"ok": False, "error": str(exc), "ports": []}

        ports = []
        for item in found:
            ports.append(
                {"port": item.port, "process": item.process, "pid": item.pid, "source": item.source}
            )
            self._log(f"进程命中 {item.process}（pid {item.pid}）监听 {item.port}")

        total = len(netlist.listening_ports())
        if not ports:
            if total == 0:
                self._log(
                    "读不到监听表。Windows 上不需要管理员；macOS/Linux 上读别的进程的套接字要 root。"
                    "改用端口扫描。"
                )
            else:
                self._log(f"没认出易启学进程。本机共有 {total} 个 TCP 监听，改用端口扫描。")
        return {"ok": True, "ports": ports, "listeners_total": total}

    # ---------------------------------------------------------------- 定位

    def locate(self, host: str, port_spec: str) -> dict:
        try:
            guard.guard_host(host)
        except guard.NonLocalError as exc:
            self._log(f"拒绝：{exc}")
            return {"ok": False, "error": f"不是本机地址：{exc}"}

        # 先按进程找，找不到再扫
        discovered = probe.discover()
        ports = [c.port for c in discovered]
        source = "process" if ports else "scan"

        if not ports:
            try:
                ports = probe.parse_ports(port_spec)
            except (ValueError, guard.NonLocalError) as exc:
                self._log(f"参数错误：{exc}")
                return {"ok": False, "error": str(exc)}

        self._log(f"{'用进程命中' if source == 'process' else '扫描'} {len(ports)} 个端口")
        result = probe.probe_candidates(host, ports)

        open_ports = [p.port for p in result.ports if p.open]
        self._log(f"开着 {len(open_ports)} 个：{open_ports or '无'}")

        hits = []
        for hit in result.hits:
            hits.append(
                {
                    "port": hit.port,
                    "path": hit.path,
                    "status": hit.status,
                    "accepted": hit.accepted,
                    "server": hit.server,
                    "matched": hit.matched,
                    "error": hit.error,
                }
            )
            verdict = "握手成功" if hit.accepted else (hit.error or f"状态 {hit.status}")
            self._log(f"  {hit.port} {hit.path} → {verdict} {hit.server}")

        return {
            "ok": True,
            "source": source,
            "open_ports": open_ports,
            "hits": hits,
            "found": result.found,
        }

    def probe_one(self, host: str, port: int) -> dict:
        try:
            port = guard.guard_port(int(port))
            guard.guard_host(host)
        except (guard.NonLocalError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

        result = probe.probe_candidates(host, [port])
        if not any(p.open for p in result.ports):
            self._log(f"{host}:{port} 连不上")
            return {"ok": True, "open": False, "hits": []}

        self._log(f"{host}:{port} 开着，试候选路径")
        hits = []
        for hit in result.hits:
            hits.append(
                {
                    "port": hit.port,
                    "path": hit.path,
                    "status": hit.status,
                    "accepted": hit.accepted,
                    "server": hit.server,
                    "matched": hit.matched,
                    "error": hit.error,
                }
            )
            verdict = "握手成功" if hit.accepted else (hit.error or f"状态 {hit.status}")
            self._log(f"  {hit.path} → {verdict}")
        return {"ok": True, "open": True, "hits": hits}

    # ---------------------------------------------------------------- 连接

    def connect(self, host: str, port: int, path: str = "/") -> dict:
        with self._lock:
            self._disconnect_locked()
            try:
                addr, p = guard.guard_target(host, int(port))
            except (guard.NonLocalError, ValueError) as exc:
                self._log(f"拒绝连接：{exc}")
                return {"ok": False, "error": f"不是本机地址：{exc}"}

            client = WebSocketClient(addr, p, path=path, timeout=3.0)
            try:
                hs = client.connect()
            except (OSError, WebSocketError) as exc:
                self._log(f"连 {addr}:{p}{path} 失败：{exc}")
                return {"ok": False, "error": str(exc)}

            if not hs.accepted:
                client.close()
                self._log(f"{addr}:{p}{path} 握手被拒，HTTP {hs.status}")
                return {"ok": False, "error": f"握手被拒，HTTP {hs.status}", "response": hs.response}

            self._client = client
            self._target = (addr, p, path)
            self._log(f"已连上 ws://{addr}:{p}{path}")
            return {"ok": True, "handshake": hs.response, "target": f"ws://{addr}:{p}{path}"}

    def disconnect(self) -> dict:
        with self._lock:
            self._disconnect_locked()
            self._log("已断开")
        return {"ok": True}

    def _disconnect_locked(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._target = None

    def status(self) -> dict:
        if self._client is None or self._target is None:
            return {"ok": True, "connected": False}
        addr, port, path = self._target
        return {
            "ok": True,
            "connected": self._client.connected,
            "target": f"ws://{addr}:{port}{path}",
        }

    # ---------------------------------------------------------------- 发送

    def _send_locked(self, method: str, params: dict) -> dict:
        """调用前必须已持锁。"""
        req = commands.build_request(method, params)
        if self._client is None or not self._client.connected:
            return {"ok": False, "error": "还没连上", "payload": req.pretty()}
        try:
            sent = self._client.send_text(req.payload)
        except (OSError, WebSocketError) as exc:
            self._log(f"发送失败：{exc}")
            return {"ok": False, "error": str(exc), "payload": req.pretty()}

        self._log(f"发出 {method}（{sent} 字节）")
        reply = None
        try:
            frame = self._client.recv_message()
            if frame is not None:
                reply = frame[1].decode("utf-8", errors="replace")
                self._log(f"收到 {len(reply)} 字节")
        except (OSError, WebSocketError) as exc:
            self._log(f"等回应超时或出错：{exc}")

        return {
            "ok": True,
            "payload": req.pretty(),
            "trace_id": req.trace_id,
            "reply": reply,
            "reply_parsed": commands.parse_response(reply) if reply else None,
        }

    def send(self, method: str, params_json: str = "") -> dict:
        if not method or not isinstance(method, str):
            return {"ok": False, "error": "method 不能空"}

        params: dict = {}
        if params_json and params_json.strip():
            try:
                loaded = json.loads(params_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"参数不是合法 JSON：{exc}"}
            if not isinstance(loaded, dict):
                return {"ok": False, "error": "参数必须是 JSON 对象"}
            params = loaded

        with self._lock:
            result = self._send_locked(method, params)

        if not result.get("ok"):
            self._log(f"发送 {method} 失败：{result.get('error')}")

        if result.get("ok"):
            result["fired"] = self._fire_rules(method)
        return result

    # ---------------------------------------------------------------- 规则

    def _fire_rules(self, triggered_by: str) -> list[dict]:
        """某条命令发出后，把匹配的规则跟出去。规则动作不再级联触发。"""
        fired: list[dict] = []
        for rule in self.rules.for_method(triggered_by):
            self._log(f"[规则] {rule.trigger} → {rule.action} ×{rule.repeat}")
            ok = True
            for index in range(rule.repeat):
                if index and rule.delay_ms:
                    time.sleep(rule.delay_ms / 1000.0)
                with self._lock:
                    out = self._send_locked(rule.action, dict(rule.params))
                if not out.get("ok"):
                    ok = False
                    self._log(f"[规则] 动作失败：{out.get('error')}")
                    break
            fired.append(
                {
                    "id": rule.id,
                    "trigger": rule.trigger,
                    "action": rule.action,
                    "repeat": rule.repeat,
                    "ok": ok,
                    "note": rule.note,
                }
            )
        return fired

    def get_rules(self) -> dict:
        return {
            "ok": True,
            "rules": self.rules.as_list(),
            "path": str(self.rules.path),
        }

    def toggle_rule(self, rule_id: str) -> dict:
        enabled = self.rules.toggle(rule_id)
        self._log(f"[规则] {rule_id} 切换为 {'开' if enabled else '关'}")
        return {"ok": True, "enabled": enabled, "rules": self.rules.as_list()}

    def add_rule(self, trigger: str, action: str, params_json: str = "",
                 repeat: int = 1, delay_ms: int = 0) -> dict:
        if not trigger.strip() or not action.strip():
            return {"ok": False, "error": "触发和动作都不能空"}
        params: dict = {}
        if params_json.strip():
            try:
                params = json.loads(params_json)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"参数不是合法 JSON：{exc}"}
            if not isinstance(params, dict):
                return {"ok": False, "error": "参数必须是 JSON 对象"}
        rule = self.rules.add(
            trigger.strip(),
            action.strip(),
            params=params,
            repeat=int(repeat or 1),
            delay_ms=int(delay_ms or 0),
        )
        self._log(f"[规则] 新增 {rule.trigger} → {rule.action}")
        return {"ok": True, "rule": rule.id, "rules": self.rules.as_list()}

    def remove_rule(self, rule_id: str) -> dict:
        removed = self.rules.remove(rule_id)
        return {"ok": removed, "rules": self.rules.as_list()}

    def reset_rules(self) -> dict:
        self.rules.reset()
        self._log("[规则] 已恢复出厂")
        return {"ok": True, "rules": self.rules.as_list()}

    # ---------------------------------------------------------------- 剪贴板

    def copy_text(self, text: str) -> dict:
        try:
            clipboard.copy_text(text if isinstance(text, str) else str(text))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}
