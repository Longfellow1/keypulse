"""Read-only legacy artifact discovery and narrowly scoped data-file cleanup."""
from __future__ import annotations

import plistlib
import shlex
from pathlib import Path
from typing import Iterable

from penpal.i18n import T
from penpal.runtime.control import LEGACY_LAUNCHD_LABELS
from penpal.utils.paths import is_migration_marker_dir


def _is_legacy_app(path: Path) -> bool:
    if path.name.casefold().startswith("keypulse"):
        return True
    info = path / "Contents" / "Info.plist"
    try:
        with info.open("rb") as handle:
            payload = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    bundle_id = str(payload.get("CFBundleIdentifier", "")).casefold()
    app_names = {
        str(payload.get(key, "")).strip().casefold()
        for key in ("CFBundleName", "CFBundleDisplayName")
    }
    return bundle_id == "com.keypulse" or bundle_id.startswith("com.keypulse.") or "keypulse" in app_names


def _why(zh: str, en: str) -> str:
    return T(zh, en)


def find_legacy_artifacts(
    home: Path,
    apps_dirs: Iterable[Path],
    agents_dir: Path,
    data_dir: Path,
) -> list[dict[str, str]]:
    """Find old KeyPulse artifacts using only the injected paths; never modifies them."""
    artifacts: list[dict[str, str]] = []
    seen_apps: set[Path] = set()
    for apps_dir in apps_dirs:
        try:
            entries = sorted(apps_dir.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for path in entries:
            if path in seen_apps or path.suffix.casefold() != ".app" or not path.is_dir():
                continue
            if not _is_legacy_app(path):
                continue
            seen_apps.add(path)
            artifacts.append({
                "kind": "legacy_app",
                "path": str(path),
                "why": _why("旧版 KeyPulse App 仍在应用程序目录中。", "An old KeyPulse app remains in an Applications folder."),
                "fix_command": f"mv {shlex.quote(str(path))} ~/.Trash/",
            })

    for label in LEGACY_LAUNCHD_LABELS:
        plist_path = agents_dir / f"{label}.plist"
        if plist_path.is_file():
            artifacts.append({
                "kind": "legacy_launchd_plist",
                "path": str(plist_path),
                "why": _why("磁盘上仍有旧版 launchd 配置。", "An old launchd plist remains on disk."),
                "fix_command": "penpal install init",
            })

    for folder in (data_dir, data_dir / "run", data_dir / "logs"):
        if folder.is_symlink() or not folder.is_dir():
            continue
        try:
            entries = sorted(folder.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for path in entries:
            name = path.name.casefold()
            if path.is_symlink() or not name.startswith("keypulse") or not path.is_file():
                continue
            if path.suffix.casefold() == ".pid":
                kind = "legacy_pid"
                why = _why("数据目录中仍有旧版 PID 文件。", "An old PID file remains in the Penpal data directory.")
            elif path.suffix.casefold() == ".log":
                kind = "legacy_log"
                why = _why("数据目录中仍有旧版 KeyPulse 日志。", "An old KeyPulse log remains in the Penpal data directory.")
            else:
                continue
            artifacts.append({
                "kind": kind,
                "path": str(path),
                "why": why,
                "fix_command": "penpal maintenance clean-legacy",
            })

    legacy_dir = home / ".keypulse"
    # The marker Penpal leaves after migrating is intentional, not clutter.
    if (legacy_dir.exists() or legacy_dir.is_symlink()) and not is_migration_marker_dir(legacy_dir, data_dir):
        artifacts.append({
            "kind": "legacy_data_dir",
            "path": str(legacy_dir),
            "why": _why(
                "旧版 ~/.keypulse 目录仍存在；请先检查其中是否只有迁移标记。",
                "The old ~/.keypulse directory remains; inspect it and confirm it contains no user data.",
            ),
            "fix_command": f"open {shlex.quote(str(legacy_dir))}",
        })

    return artifacts


def clean_legacy_data_files(data_dir: Path) -> list[Path]:
    """Delete only regular KeyPulse-named PID/log files inside Penpal's data dir."""
    removed: list[Path] = []
    for folder in (data_dir, data_dir / "run", data_dir / "logs"):
        if folder.is_symlink() or not folder.is_dir():
            continue
        try:
            entries = tuple(folder.iterdir())
        except FileNotFoundError:
            continue
        for path in entries:
            if path.is_symlink() or not path.is_file() or not path.name.casefold().startswith("keypulse"):
                continue
            if path.suffix.casefold() not in {".pid", ".log"}:
                continue
            path.unlink()
            removed.append(path)
    return removed
