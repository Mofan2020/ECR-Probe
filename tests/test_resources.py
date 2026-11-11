"""资源释放与校验的测试。

这块的核心承诺是"校验不通过就整目录替换，绝不半新半旧地启动"。
所以要专门测那些"看起来文件都在、内容已经不对"的情形。
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

from ecr_probe import resources


@pytest.fixture()
def fake_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """把持久目录钉到 tmp_path，绝不碰真实的 Application Support。"""
    root = tmp_path / "appdata"
    monkeypatch.setattr(resources, "app_data_dir", lambda: root)
    return root


@pytest.fixture()
def source(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """造一份"打包进来的"资源。"""
    src = tmp_path / "bundle" / "gui"
    (src / "assets").mkdir(parents=True)
    (src / "index.html").write_text("<html>hello</html>", encoding="utf-8")
    (src / "style.css").write_text("body{}", encoding="utf-8")
    (src / "app.js").write_text("console.log(1)", encoding="utf-8")
    (src / "assets" / "x.txt").write_text("nested", encoding="utf-8")
    monkeypatch.setattr(resources, "bundled_dir", lambda: src)
    return src


# ------------------------------------------------------------------ 路径


def test_app_data_dir_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setenv("ECR_PROBE_HOME", str(tmp_path / "custom"))
    assert resources.app_data_dir() == tmp_path / "custom"


def test_app_data_dir_platform_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """目录名跟平台惯例走：Windows/macOS 用 ECR-Probe，Linux 用小写。

    这不是随便定的——Linux 上应用数据目录用小写是惯例，Windows 和 macOS
    用显示名也是惯例。测试要跟着平台走，不能写死一个。
    """
    monkeypatch.delenv("ECR_PROBE_HOME", raising=False)
    got = resources.app_data_dir()
    expected = "ecr-probe" if sys.platform not in {"win32", "darwin"} else "ECR-Probe"
    assert got.name == expected


def test_bundled_dir_found_in_source_tree() -> None:
    """源码态应该找得到 ecr_probe/gui。"""
    found = resources.bundled_dir()
    assert found is not None
    assert (found / "index.html").is_file()


def test_bundled_dir_returns_none_when_missing(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resources, "__file__", str(tmp_path / "nowhere" / "resources.py"))
    monkeypatch.setattr(resources.sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    assert resources.bundled_dir() is None


# ------------------------------------------------------------------ 摘要


def test_manifest_covers_every_file(source: pathlib.Path) -> None:
    manifest = resources.compute_manifest(source)
    assert set(manifest) == {"index.html", "style.css", "app.js", "assets/x.txt"}
    assert all(len(v) == 64 for v in manifest.values())


def test_manifest_is_deterministic(source: pathlib.Path) -> None:
    assert resources.compute_manifest(source) == resources.compute_manifest(source)


def test_manifest_changes_when_content_changes(source: pathlib.Path) -> None:
    before = resources.compute_manifest(source)
    (source / "app.js").write_text("console.log(2)", encoding="utf-8")
    after = resources.compute_manifest(source)
    assert before["app.js"] != after["app.js"]


def test_manifest_rejects_missing_dir(tmp_path: pathlib.Path) -> None:
    with pytest.raises(resources.ResourceError):
        resources.compute_manifest(tmp_path / "nope")


def test_manifest_rejects_empty_dir(tmp_path: pathlib.Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(resources.ResourceError, match="空的"):
        resources.compute_manifest(empty)


# ------------------------------------------------------------------ 释放


def test_first_run_creates_target(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    target = resources.ensure_gui()
    assert target == fake_home / "resources"
    assert (target / "index.html").is_file()
    assert (target / "assets" / "x.txt").is_file()
    assert (target / resources.MANIFEST_NAME).is_file()


def test_second_run_reuses_without_rewriting(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    target = resources.ensure_gui()
    stamp = (target / "index.html").stat().st_mtime_ns
    resources.ensure_gui()
    assert (target / "index.html").stat().st_mtime_ns == stamp, "校验通过却重写了文件"


def test_edited_file_is_replaced(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    """用户手改了界面文件 → 下次启动整目录替换回原样。"""
    target = resources.ensure_gui()
    (target / "app.js").write_text("// 被我改坏了", encoding="utf-8")

    resources.ensure_gui()
    assert (target / "app.js").read_text(encoding="utf-8") == "console.log(1)"


def test_deleted_file_is_restored(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    target = resources.ensure_gui()
    (target / "style.css").unlink()
    resources.ensure_gui()
    assert (target / "style.css").is_file()


def test_extra_file_triggers_replace(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    """多出来的文件也算不一致——残留的旧版本文件是最难查的那种坑。"""
    target = resources.ensure_gui()
    (target / "old-app.js").write_text("// 上一版残留", encoding="utf-8")

    resources.ensure_gui()
    assert not (target / "old-app.js").exists()


def test_corrupt_manifest_triggers_replace(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    target = resources.ensure_gui()
    (target / resources.MANIFEST_NAME).write_text("{broken", encoding="utf-8")

    resources.ensure_gui()
    ok, _ = resources.verify(target, resources.compute_manifest(source))
    assert ok


def test_missing_manifest_triggers_replace(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    target = resources.ensure_gui()
    (target / resources.MANIFEST_NAME).unlink()

    resources.ensure_gui()
    assert (target / resources.MANIFEST_NAME).is_file()


def test_new_bundle_version_replaces(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    """打包资源换了（升级版本），持久目录要跟着换。"""
    resources.ensure_gui()
    (source / "app.js").write_text("console.log('v2')", encoding="utf-8")

    target = resources.ensure_gui()
    assert (target / "app.js").read_text(encoding="utf-8") == "console.log('v2')"


def test_force_replaces_even_when_clean(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    """强制释放要真的换掉目录内容。

    不能拿 mtime 判断：copy2 会保留源文件的 mtime，换没换都一个值。
    放个哨兵文件进去，它消失了才说明目录被整体换过。
    """
    target = resources.ensure_gui()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("x", encoding="utf-8")

    resources.ensure_gui(force=True)
    assert not sentinel.exists(), "强制释放没有真的替换目录"


def test_no_staging_left_behind(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    """临时目录不能留在现场。"""
    resources.ensure_gui()
    leftovers = [p for p in fake_home.iterdir() if p.name.startswith(".resources")]
    assert not leftovers


def test_missing_bundle_raises(fake_home: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resources, "bundled_dir", lambda: None)
    with pytest.raises(resources.ResourceError, match="找不到"):
        resources.ensure_gui()


# ------------------------------------------------------------------ 校验


def test_verify_reports_missing_dir(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    ok, reason = resources.verify(fake_home / "nope", resources.compute_manifest(source))
    assert not ok
    assert "不存在" in reason


def test_verify_reports_missing_files_by_name(
    fake_home: pathlib.Path, source: pathlib.Path
) -> None:
    """空目录要直接说出缺哪些文件，不是笼统的"校验失败"。"""
    target = fake_home / "resources"
    target.mkdir(parents=True)
    ok, reason = resources.verify(target, resources.compute_manifest(source))
    assert not ok
    assert "缺" in reason
    assert "app.js" in reason


def test_verify_reports_missing_manifest_when_files_match(
    fake_home: pathlib.Path, source: pathlib.Path
) -> None:
    """文件都在、内容和打包资源一致，只是摘要文件没了 —— 也算不一致。"""
    target = resources.ensure_gui()
    (target / resources.MANIFEST_NAME).unlink()

    ok, reason = resources.verify(target, resources.compute_manifest(source))
    assert not ok
    assert "摘要" in reason


def test_verify_reports_changed_file(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    target = resources.ensure_gui()
    (target / "app.js").write_text("tampered", encoding="utf-8")
    ok, reason = resources.verify(target, resources.compute_manifest(source))
    assert not ok
    assert "app.js" in reason, "原因要指到具体文件，不然没法查"


def test_verify_passes_on_fresh_extract(fake_home: pathlib.Path, source: pathlib.Path) -> None:
    target = resources.ensure_gui()
    ok, reason = resources.verify(target, resources.compute_manifest(source))
    assert ok, reason


def test_manifest_file_not_hashed_into_itself(
    fake_home: pathlib.Path, source: pathlib.Path
) -> None:
    """摘要文件自己不能被算进摘要，否则每次写都变。"""
    target = resources.ensure_gui()
    first = resources.read_manifest(target)
    resources.ensure_gui(force=True)
    second = resources.read_manifest(target)
    assert first == second


def test_edited_manifest_with_right_content_still_fails(
    fake_home: pathlib.Path, source: pathlib.Path
) -> None:
    """把摘要文件改成"看起来对"但列错文件，也要判不一致。"""
    target = resources.ensure_gui()
    path = target / resources.MANIFEST_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"].pop("app.js")
    path.write_text(json.dumps(data), encoding="utf-8")

    ok, _ = resources.verify(target, resources.compute_manifest(source))
    assert not ok
