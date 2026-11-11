"""自动化规则：收到/发出某条命令时，自动跟一条动作命令。

为什么需要：有些状态光靠一条命令改不干净——屏幕广播一开，学生端会
自己进全屏，得有人再补一条切窗口的。规则引擎就是那个"补一条"。

规则形状：trigger（触发方法名，支持结尾 * 通配）→ action（要发的方法）。
可以配连发次数和间隔，因为对端可能只认第一帧。

默认规则里的动作方法是**推断**的：displayMode 这个键在配置表里确认存在，
但取什么值表示窗口、以及哪条命令负责改它，都没有验证过。所以默认给
三条候选，只开第一条，其余留给你自己试。
"""

from __future__ import annotations

import json
import pathlib
import uuid
from dataclasses import asdict, dataclass, field


def _default_dir() -> pathlib.Path:
    return pathlib.Path.home() / ".ecr-probe"


@dataclass
class Rule:
    trigger: str
    action: str
    params: dict = field(default_factory=dict)
    enabled: bool = True
    repeat: int = 1
    delay_ms: int = 0
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    def matches(self, method: str) -> bool:
        """触发匹配。结尾 * 表示前缀匹配。"""
        if not self.trigger:
            return False
        if self.trigger.endswith("*"):
            return method.startswith(self.trigger[:-1])
        return method == self.trigger


def default_rules() -> list[Rule]:
    """出厂规则。

    第一条是用户要的那个场景：开始屏幕广播 → 立刻把学生端从全屏切回窗口。
    """
    return [
        Rule(
            trigger="local.teacher->student.startBroadcast",
            action="local.teacher->student.setProperty",
            params={"displayMode": "window", "builtinRendering": True},
            enabled=True,
            repeat=3,
            delay_ms=400,
            note="广播开始后切窗口。displayMode 的取值未验证，若不生效换另外两条候选。",
        ),
        Rule(
            trigger="local.teacher->student.startReceiveBroadcast",
            action="local.teacher->student.setProperty",
            params={"displayMode": "window"},
            enabled=True,
            repeat=3,
            delay_ms=400,
            note="接收广播后切窗口，同上。",
        ),
        Rule(
            trigger="local.teacher->student.startBroadcast",
            action="local.teacher->student.broadcast.control",
            params={"displayMode": "window"},
            enabled=False,
            repeat=2,
            delay_ms=300,
            note="候选二：走 broadcast.control。默认关，试第一条不通再开这个。",
        ),
        Rule(
            trigger="local.teacher->student.startBroadcast",
            action="student.broadcast.viewControl",
            params={"displayMode": "window"},
            enabled=False,
            repeat=2,
            delay_ms=300,
            note="候选三：走 student.broadcast.viewControl。同上。",
        ),
        Rule(
            trigger="student.desktopBroadcast.start",
            action="student.desktopBroadcast.close",
            params={},
            enabled=False,
            note="关掉学生桌面广播。危险动作，默认关。",
        ),
    ]


class RuleStore:
    """规则集合，带落盘。文件坏了就当空的，不能因为读不到规则就崩。"""

    def __init__(self, path: pathlib.Path | None = None) -> None:
        self.path = path or (_default_dir() / "rules.json")
        self.rules: list[Rule] = []
        self.load()

    # ---------- 持久化 ----------

    def load(self) -> None:
        if not self.path.exists():
            self.rules = default_rules()
            self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.rules = default_rules()
            return
        rules: list[Rule] = []
        for item in raw.get("rules", []) if isinstance(raw, dict) else []:
            try:
                rules.append(
                    Rule(
                        trigger=str(item["trigger"]),
                        action=str(item["action"]),
                        params=dict(item.get("params") or {}),
                        enabled=bool(item.get("enabled", True)),
                        repeat=max(1, int(item.get("repeat", 1))),
                        delay_ms=max(0, int(item.get("delay_ms", 0))),
                        note=str(item.get("note", "")),
                        id=str(item.get("id") or uuid.uuid4().hex[:8]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        self.rules = rules or default_rules()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"rules": [asdict(r) for r in self.rules]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    # ---------- 查询 ----------

    def for_method(self, method: str) -> list[Rule]:
        return [r for r in self.rules if r.enabled and r.matches(method)]

    def get(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def as_list(self) -> list[dict]:
        return [asdict(r) for r in self.rules]

    # ---------- 修改 ----------

    def toggle(self, rule_id: str) -> bool:
        rule = self.get(rule_id)
        if rule is None:
            return False
        rule.enabled = not rule.enabled
        self.save()
        return rule.enabled

    def add(self, trigger: str, action: str, params: dict | None = None,
            repeat: int = 1, delay_ms: int = 0, note: str = "") -> Rule:
        rule = Rule(
            trigger=trigger,
            action=action,
            params=dict(params or {}),
            repeat=max(1, repeat),
            delay_ms=max(0, delay_ms),
            note=note,
        )
        self.rules.append(rule)
        self.save()
        return rule

    def remove(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.id != rule_id]
        if len(self.rules) != before:
            self.save()
            return True
        return False

    def reset(self) -> None:
        self.rules = default_rules()
        self.save()
