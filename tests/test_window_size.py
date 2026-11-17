"""窗口尺寸适配屏幕。

窗口比屏幕还大的话，底部那截永远看不到，只能靠拖边框，macOS 上干脆
拖不出来。界面本身做了滚动，所以窗口小一点也不影响用。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from ecr_probe import __main__ as main


class _Screen:
    """pywebview.Screen 的最小替身。"""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height


def _patch_screens(monkeypatch: pytest.MonkeyPatch, screens: list[_Screen]) -> None:
    """把 webview.screens 换成假的。

    webview 在这里只是拿来读屏幕尺寸，不该在无界面环境里真初始化。
    """
    import webview

    monkeypatch.setattr(webview, "screens", screens, raising=False)


def test_small_screen_shrinks_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """屏幕比默认窗口小，窗口要收进来。"""
    _patch_screens(monkeypatch, [_Screen(1366, 768)])
    width, height = main._fit_to_screen(1180, 820)
    assert width <= 1366
    assert height <= 768


def test_large_screen_keeps_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """屏幕够大就别缩，原来的尺寸挺好。"""
    _patch_screens(monkeypatch, [_Screen(2560, 1440)])
    width, height = main._fit_to_screen(1180, 820)
    assert (width, height) == (1180, 820)


def test_multi_screen_picks_the_biggest(monkeypatch: pytest.MonkeyPatch) -> None:
    """接了两块屏，按最大的那块算。

    故意用一块放不下默认窗口的小屏当第二块：取错了就会超屏，
    取对了才放得下 1180x820。
    """
    _patch_screens(monkeypatch, [_Screen(1024, 768), _Screen(2560, 1440)])
    width, height = main._fit_to_screen(1180, 820)
    assert (width, height) == (1180, 820), "得按大屏算，不能按小屏"


def test_cmd_gui_uses_the_fitted_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """cmd_gui 得真的用算出来的尺寸去开窗。

    只测 _fit_to_screen 是不够的：函数算对了但没人调用它，
    窗口照样是固定 1180x820。变异「窗口不适配屏幕」就是从这儿钻过去的。
    """
    created: dict[str, object] = {}

    class _FakeWindow:
        def __init__(self, *args: object, **kwargs: object) -> None:
            created.update(kwargs)

    class _FakeWebview:
        def __init__(self) -> None:
            self.screens = [_Screen(1366, 768)]

        @staticmethod
        def create_window(*args: object, **kwargs: object) -> _FakeWindow:
            return _FakeWindow(*args, **kwargs)

        @staticmethod
        def start(*args: object, **kwargs: object) -> None:
            created["started"] = True

    monkeypatch.setitem(sys.modules, "webview", _FakeWebview)

    import webview

    monkeypatch.setattr(webview, "screens", [_Screen(1366, 768)], raising=False)

    args = main.build_parser().parse_args(["gui"])
    # 资源校验那一段会真去释放文件，这里绕开，只测开窗参数
    monkeypatch.setattr(
        "ecr_probe.resources.ensure_gui",
        lambda force=False: pathlib.Path("/tmp/ecr-probe-fake-gui"),
    )
    monkeypatch.setattr(main, "Bridge", lambda: object())
    monkeypatch.setattr(main, "_ensure_output_encoding", lambda: None)

    assert main.cmd_gui(args) == 0
    assert created.get("started") is True
    width = created.get("width")
    height = created.get("height")
    assert isinstance(width, int) and isinstance(height, int)
    assert width <= 1366 and height <= 768, "窗口还是超屏"


def test_never_goes_below_usable_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    """屏幕极小的时候也得留一个能用的下限，不能算出 0。"""
    _patch_screens(monkeypatch, [_Screen(200, 150)])
    width, height = main._fit_to_screen(1180, 820)
    assert width >= 640
    assert height >= 480


def test_falls_back_when_screens_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """拿不到屏幕信息就退回原尺寸，不能因此开不出窗口。"""
    import webview

    class _Boom:
        def __get__(self, obj: object, owner: type) -> list[_Screen]:
            raise RuntimeError("没有屏幕信息")

    monkeypatch.setattr(webview, "screens", _Boom(), raising=False)
    assert main._fit_to_screen(1180, 820) == (1180, 820)


def test_empty_screen_list_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_screens(monkeypatch, [])
    assert main._fit_to_screen(1180, 820) == (1180, 820)
