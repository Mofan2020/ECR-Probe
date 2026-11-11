"""启动时把界面资源释放到持久目录，每次启动校验，校验通过才加载。

为什么不用 PyInstaller 自带的 _MEIPASS：那是临时目录，每次启动换一个
路径，进程一退就清。界面资源放那儿等于每次都要重新落盘，也没法让用户
看到"到底加载的是哪个版本的文件"。

这里的做法：

1. 把打包进来的 gui/ 复制到持久目录（%LOCALAPPDATA% / Application Support）；
2. 同时写一份 .resources.digest，逐文件记 sha256；
3. 每次启动重算并比对。一致就直接用，不一致（文件被改过、或者换了新
   版本）就整目录替换，不做逐文件补丁。

整目录替换而不是逐个修，是因为半新半旧的状态最难查：界面报错但文件都
"在"，排查起来是无底洞。宁可多复制几十 KB。

替换走「写到临时目录 → 再整体换名」，中途被杀不会留下半个目录。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import sys
import time

MANIFEST_NAME = ".resources.digest"
_GUI_SUBDIR = "gui"


class ResourceError(RuntimeError):
    """资源释放或校验失败。启动应该停在这里，不要带着坏资源硬跑。"""


# ------------------------------------------------------------------ 路径


def app_data_dir() -> pathlib.Path:
    """持久目录。ECR_PROBE_HOME 可覆盖（测试和便携用途）。"""
    override = os.environ.get("ECR_PROBE_HOME")
    if override:
        return pathlib.Path(override).expanduser()

    home = pathlib.Path.home()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return pathlib.Path(base) / "ECR-Probe" if base else home / "AppData" / "Local" / "ECR-Probe"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "ECR-Probe"
    return home / ".local" / "share" / "ecr-probe"


def resources_dir() -> pathlib.Path:
    return app_data_dir() / "resources"


def bundled_dir() -> pathlib.Path | None:
    """打包后资源在哪。

    冻结态在 sys._MEIPASS/gui；源码态在包的 gui/ 目录。
    两处都没有说明装坏了，返回 None 让调用方报错。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = pathlib.Path(meipass) / _GUI_SUBDIR
        if candidate.is_dir():
            return candidate

    candidate = pathlib.Path(__file__).parent / _GUI_SUBDIR
    if candidate.is_dir():
        return candidate
    return None


# ------------------------------------------------------------------ 摘要


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_manifest(root: pathlib.Path) -> dict[str, str]:
    """逐文件哈希。键是相对路径（用 / 分隔），值是小写 sha256。"""
    if not root.is_dir():
        raise ResourceError(f"资源目录不存在：{root}")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == MANIFEST_NAME:
            continue
        rel = path.relative_to(root).as_posix()
        files[rel] = _sha256(path)
    if not files:
        raise ResourceError(f"资源目录是空的：{root}")
    return files


def read_manifest(root: pathlib.Path) -> dict[str, str] | None:
    """读已落盘的摘要。文件缺失或坏了返回 None（等于强制重装）。"""
    path = root / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    files = data.get("files")
    if not isinstance(files, dict):
        return None
    return {str(k): str(v) for k, v in files.items()}


def verify(root: pathlib.Path, expected: dict[str, str]) -> tuple[bool, str]:
    """比对磁盘上的实际内容和期望摘要。

    返回 (是否一致, 原因)。不一致的原因要能直接看懂，不然出问题只会说
    "校验失败"而查不出是哪个文件。
    """
    if not root.is_dir():
        return False, "目录不存在"

    # 先看磁盘上到底有哪些文件。
    # 不能只读摘要文件里那份清单——那样多出来的残留文件（上一版留下的
    # old-app.js 之类）就完全看不见了，"整目录替换"的承诺是假的。
    on_disk = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST_NAME
    }

    if on_disk != set(expected):
        missing = sorted(set(expected) - on_disk)
        extra = sorted(on_disk - set(expected))
        detail = []
        if missing:
            detail.append(f"缺 {len(missing)} 个：{', '.join(missing[:3])}")
        if extra:
            detail.append(f"多 {len(extra)} 个：{', '.join(extra[:3])}")
        return False, "文件清单和打包资源不一致（" + "；".join(detail) + "）"

    manifest = read_manifest(root)
    if manifest is None:
        return False, "没有摘要文件"
    if manifest != expected:
        return False, "摘要内容和打包资源不一致"

    for rel, want in expected.items():
        path = root / rel
        if not path.is_file():
            return False, f"{rel} 不见了"
        if _sha256(path) != want:
            return False, f"{rel} 内容被改过"

    return True, "一致"


# ------------------------------------------------------------------ 释放


def _write_manifest(root: pathlib.Path, files: dict[str, str]) -> None:
    payload = {
        "files": files,
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    (root / MANIFEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _replace_whole_dir(source: pathlib.Path, target: pathlib.Path, files: dict[str, str]) -> None:
    """写到临时目录再整体换名。中途被杀不会留下半个 target。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.new-{os.getpid()}"

    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    try:
        for rel in files:
            src = source / rel
            dst = staging / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        _write_manifest(staging, files)

        # 校验自己刚写出来的东西，别把坏副本换上去
        ok, reason = verify(staging, files)
        if not ok:
            raise ResourceError(f"写出的资源自检失败：{reason}")

        if target.exists():
            shutil.rmtree(target)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def ensure_gui(force: bool = False) -> pathlib.Path:
    """把界面资源准备好，返回可以直接加载的目录。

    一致就复用，不一致整目录替换。替换失败抛 ResourceError——
    宁可起不来，也不要拿半套资源起一个看起来正常、点下去全错的界面。
    """
    source = bundled_dir()
    if source is None:
        raise ResourceError(
            "找不到打包进去的界面资源。源码运行时应有 ecr_probe/gui/，"
            "打包运行时应有 _MEIPASS/gui/。"
        )

    expected = compute_manifest(source)
    target = resources_dir()

    if not force:
        ok, _reason = verify(target, expected)
        if ok:
            return target

    _replace_whole_dir(source, target, expected)
    return target
