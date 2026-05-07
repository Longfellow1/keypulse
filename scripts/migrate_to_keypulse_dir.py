#!/usr/bin/env python3
"""Migrate vault Events/Topics to ~/.keypulse/events|topics.

Default mode is dry-run. Pass --apply for real migration.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keypulse.config import Config  # noqa: E402


_GUIDANCE_TEXT = """[migration guidance]
1) stop daemon first:
   launchctl unload ~/Library/LaunchAgents/com.keypulse.daemon.plist
2) run migration:
   python3 scripts/migrate_to_keypulse_dir.py --apply
3) start daemon again:
   launchctl load ~/Library/LaunchAgents/com.keypulse.daemon.plist
"""


@dataclass
class RewriteResult:
    file: Path
    changes: int


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate vault Events/Topics into ~/.keypulse directory.")
    parser.add_argument(
        "--vault",
        default=Config.load().obsidian.vault_path,
        help="Vault path (default from config [obsidian].vault_path)",
    )
    parser.add_argument("--dry-run", action="store_true", default=True, help="Preview only (default).")
    parser.add_argument("--apply", action="store_true", help="Apply migration and rewrite links.")
    return parser.parse_args()


def rewrite_wiki_links(text: str) -> tuple[str, int]:
    changes = 0

    def _replace(match):
        nonlocal changes
        target = (match.group("target") or "").strip()
        label = match.group("label") or ""
        lower = target.lower()

        if lower.startswith("../.keypulse/events/") or lower.startswith("../.keypulse/topics/"):
            return match.group(0)
        if lower.startswith("events/"):
            changes += 1
            return f"[[../.keypulse/events/{target[7:]}{label}]]"
        if lower.startswith("topics/"):
            changes += 1
            return f"[[../.keypulse/topics/{target[7:]}{label}]]"
        return match.group(0)

    import re

    pattern = re.compile(r"\[\[(?P<target>[^\]|]+)(?P<label>\|[^\]]+)?\]\]")
    rewritten = pattern.sub(_replace, text)
    return rewritten, changes


def _backup_vault(vault: Path) -> Path:
    backup_dir = vault / ".keypulse-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"vault-{date.today().isoformat()}.tar.gz"
    with tarfile.open(backup_path, "w:gz") as tar:
        for child in sorted(vault.iterdir()):
            if child.name == ".keypulse-backup":
                continue
            tar.add(child, arcname=child.name)
    return backup_path


def _move_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        shutil.rmtree(src)
    else:
        shutil.move(str(src), str(dst))


def _rewrite_daily_weekly(vault: Path, *, apply: bool) -> tuple[list[dict[str, Any]], int]:
    updated_files: list[dict[str, Any]] = []
    total_changes = 0
    candidates = list((vault / "Daily").rglob("*.md")) + list((vault / "Weekly").rglob("*.md"))

    for path in sorted(candidates):
        if not path.exists() or not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        rewritten, changes = rewrite_wiki_links(original)
        if changes == 0:
            continue
        total_changes += changes
        updated_files.append({"path": str(path), "changes": changes})
        if apply:
            path.write_text(rewritten, encoding="utf-8")

    return updated_files, total_changes


def run_migration(vault_path: Path, *, apply: bool, keypulse_home: Path | None = None) -> dict[str, Any]:
    vault = vault_path.expanduser().resolve()
    if not vault.exists():
        raise FileNotFoundError(f"vault not found: {vault}")

    kp_home = (keypulse_home or (Path.home() / ".keypulse")).expanduser().resolve()
    source_events = vault / "Events"
    source_topics = vault / "Topics"
    target_events = kp_home / "events"
    target_topics = kp_home / "topics"

    updated_files, link_changes = _rewrite_daily_weekly(vault, apply=False)

    backup_path: Path | None = None
    moved_events = bool(source_events.exists())
    moved_topics = bool(source_topics.exists())

    if apply:
        backup_path = _backup_vault(vault)
        _move_tree(source_events, target_events)
        _move_tree(source_topics, target_topics)
        updated_files, link_changes = _rewrite_daily_weekly(vault, apply=True)

    return {
        "mode": "apply" if apply else "dry-run",
        "vault_path": str(vault),
        "keypulse_home": str(kp_home),
        "source": {
            "events": str(source_events),
            "topics": str(source_topics),
        },
        "target": {
            "events": str(target_events),
            "topics": str(target_topics),
        },
        "planned": {
            "move_events": moved_events,
            "move_topics": moved_topics,
        },
        "link_rewrites": {
            "files": updated_files,
            "total_changes": link_changes,
        },
        "backup_path": str(backup_path) if backup_path else None,
    }


def main() -> int:
    args = _parse_args()
    apply = bool(args.apply)
    vault_path = Path(args.vault)

    print(_GUIDANCE_TEXT)
    report = run_migration(vault_path, apply=apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
