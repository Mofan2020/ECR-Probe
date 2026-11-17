"""前端契约测试：app.js 调用的每个桥方法必须在 Bridge 上真的存在。

没这层检查，界面点下去会静默失败——JS 调一个不存在的方法不会报错到
控制台以外的地方，用户只会觉得"按钮没反应"。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from ecr_probe.bridge import Bridge

GUI_DIR = pathlib.Path(__file__).parent.parent / "ecr_probe" / "gui"
APP_JS = GUI_DIR / "app.js"


def called_methods() -> set[str]:
    source = APP_JS.read_text(encoding="utf-8")
    # 匹配 api().xxx(  和 window.pywebview.api.xxx(
    found = set(re.findall(r"api\(\)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", source))
    found |= set(re.findall(r"pywebview\.api\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", source))
    return found


def test_app_js_exists() -> None:
    assert APP_JS.exists()
    assert (GUI_DIR / "index.html").exists()
    assert (GUI_DIR / "style.css").exists()


def test_every_js_call_exists_on_bridge() -> None:
    bridge = Bridge(rule_path=pathlib.Path("/tmp/ecr-probe-contract.json"))
    missing = sorted(
        name for name in called_methods() if not callable(getattr(bridge, name, None))
    )
    assert not missing, f"app.js 调用了 Bridge 上不存在的方法：{missing}"


def test_js_actually_calls_something() -> None:
    """正则写错会让上面那个测试永远绿，这条防它。"""
    assert len(called_methods()) >= 8


def test_copy_buttons_have_targets() -> None:
    """每个 data-copy 的按钮，index.html 里都要有对应的输出元素。"""
    html = (GUI_DIR / "index.html").read_text(encoding="utf-8")
    js = (GUI_DIR / "app.js").read_text(encoding="utf-8")

    targets = set(re.findall(r'data-copy="([a-z]+)"', html))
    assert targets, "一个复制按钮都没有"

    expected = {"payload", "reply", "log"}
    assert expected <= targets, f"缺复制按钮：{expected - targets}"

    # app.js 的 source 映射要覆盖全部目标
    for name in targets:
        assert f'id="{name}"' in html or name in js, f"{name} 没有对应的输出元素"


def test_scrollable_containers_present() -> None:
    """用户要求：显示不下要能滚动。检查关键容器都带 scroll 类。"""
    html = (GUI_DIR / "index.html").read_text(encoding="utf-8")
    for element_id in ("groups", "log", "payload", "reply", "hits", "rules"):
        match = re.search(rf'id="{element_id}"[^>]*class="([^"]*)"', html)
        assert match, f"{element_id} 不存在"
        assert "scroll" in match.group(1), f"{element_id} 没带 scroll 类，会溢出被截断"


def test_main_rows_are_content_sized() -> None:
    """面板不能被压到内容高度以下。

    main 是 grid，行高默认按剩余空间平分。窗口一矮，每块面板就被压扁，
    超出部分既被裁掉又滚不到——用户看到的是缺了一截的界面。
    行高按内容算，页面整体滚动。
    """
    css = (GUI_DIR / "style.css").read_text(encoding="utf-8")
    main_block = re.search(r"^main\s*\{(.*?)\}", css, re.S | re.M)
    assert main_block, "找不到 main 的样式块"
    body = main_block.group(1)
    assert "grid-auto-rows: max-content" in body, "main 的行高又变回平分了"
    assert "overflow-y: auto" in body, "main 不能滚"


def test_main_scrolls_rather_than_clips() -> None:
    """body 自己不滚，滚的是 main。body 一滚，页头页脚就会跟着跑掉。"""
    css = (GUI_DIR / "style.css").read_text(encoding="utf-8")
    body_block = re.search(r"^body\s*\{(.*?)\}", css, re.S | re.M)
    assert body_block, "找不到 body 的样式块"
    assert "overflow: hidden" in body_block.group(1)


def test_localhost_default_in_html() -> None:
    """默认主机必须是回环地址。"""
    html = (GUI_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="host" value="127.0.0.1"' in html


@pytest.mark.parametrize("name", ["connect", "send", "locate", "discover", "copy_text", "get_rules"])
def test_core_bridge_methods_present(name: str) -> None:
    bridge = Bridge(rule_path=pathlib.Path("/tmp/ecr-probe-contract.json"))
    assert callable(getattr(bridge, name, None))
