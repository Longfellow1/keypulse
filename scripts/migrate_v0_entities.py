#!/usr/bin/env python3
"""Backfill raw_events.metadata_json.entities for M0.

Default mode is dry-run. Pass --apply to persist changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keypulse.config import Config  # noqa: E402


_COMMIT_RE = re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b", re.IGNORECASE)


@dataclass
class Stats:
    scanned: int = 0
    parse_failed: int = 0
    eligible: int = 0
    patched_rows: int = 0
    unchanged_rows: int = 0
    commit_added_rows: int = 0
    urls_added_rows: int = 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill raw_events.metadata_json.entities (M0).")
    parser.add_argument(
        "--db",
        default=Config.load().db_path_expanded.as_posix(),
        help="SQLite database path (default from config)",
    )
    parser.add_argument("--dry-run", action="store_true", default=True, help="Preview only (default).")
    parser.add_argument("--apply", action="store_true", help="Persist updates to DB.")
    return parser.parse_args()


def _normalize_url_without_query(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url.split("?", 1)[0].split("#", 1)[0]
    if not parsed.scheme or not parsed.netloc:
        return url.split("?", 1)[0].split("#", 1)[0]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _extract_commit_hash(metadata: dict[str, Any]) -> str | None:
    for key in ("full_hash", "commit_hash"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = json.dumps(metadata, ensure_ascii=False)
    match = _COMMIT_RE.search(text)
    if not match:
        return None
    return match.group(0)


def _extract_urls(metadata: dict[str, Any], entities: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("url", "full_url"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    raw_urls = entities.get("urls")
    if isinstance(raw_urls, list):
        for value in raw_urls:
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    normalized = [_normalize_url_without_query(value) for value in candidates]
    cleaned = [value for value in normalized if value]
    return _dedupe_keep_order(cleaned)


def _patch_entities(metadata: dict[str, Any]) -> tuple[dict[str, Any], bool, bool, bool]:
    entities_raw = metadata.get("entities")
    entities: dict[str, Any]
    if isinstance(entities_raw, dict):
        entities = dict(entities_raw)
    else:
        entities = {}

    changed = False
    commit_added = False
    urls_added = False

    if "commit_hash" not in entities:
        commit_hash = _extract_commit_hash(metadata)
        if commit_hash:
            entities["commit_hash"] = commit_hash
            changed = True
            commit_added = True

    urls = _extract_urls(metadata, entities)
    if urls:
        current_urls = entities.get("urls")
        if current_urls != urls:
            entities["urls"] = urls
            changed = True
            urls_added = True

    if changed or "entities" not in metadata:
        metadata["entities"] = entities
        changed = True

    return metadata, changed, commit_added, urls_added


def run_migration(db_path: Path, *, apply: bool) -> Stats:
    stats = Stats()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if apply:
        conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute("SELECT id, metadata_json FROM raw_events ORDER BY id ASC").fetchall()
        for row in rows:
            stats.scanned += 1
            raw_metadata = row["metadata_json"]
            if not raw_metadata:
                continue
            try:
                parsed = json.loads(raw_metadata)
            except json.JSONDecodeError:
                stats.parse_failed += 1
                continue
            if not isinstance(parsed, dict):
                stats.parse_failed += 1
                continue
            stats.eligible += 1

            updated, changed, commit_added, urls_added = _patch_entities(dict(parsed))
            if not changed:
                stats.unchanged_rows += 1
                continue
            if commit_added:
                stats.commit_added_rows += 1
            if urls_added:
                stats.urls_added_rows += 1
            stats.patched_rows += 1
            if apply:
                conn.execute(
                    "UPDATE raw_events SET metadata_json = ? WHERE id = ?",
                    (json.dumps(updated, ensure_ascii=False), row["id"]),
                )
        if apply:
            conn.commit()
    finally:
        conn.close()
    return stats


def _render_report(stats: Stats, *, apply: bool, db_path: Path) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    success_base = stats.eligible if stats.eligible > 0 else 1
    success_rate = (stats.patched_rows / success_base) * 100.0
    return "\n".join(
        [
            f"[migrate_v0_entities] mode={mode}",
            f"db={db_path}",
            f"scanned={stats.scanned} eligible={stats.eligible} parse_failed={stats.parse_failed}",
            f"patched_rows={stats.patched_rows} unchanged_rows={stats.unchanged_rows}",
            f"commit_added_rows={stats.commit_added_rows} urls_added_rows={stats.urls_added_rows}",
            f"success_rate={success_rate:.2f}% (patched_rows / eligible)",
        ]
    )


def main() -> int:
    args = _parse_args()
    apply = bool(args.apply)
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"database not found: {db_path}")
        return 2
    stats = run_migration(db_path, apply=apply)
    print(_render_report(stats, apply=apply, db_path=db_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
