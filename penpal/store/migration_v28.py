"""Migration v28 (issue #57 / M2): evidence rows for rows ingested before every source had a locator.

M1 found that chrome_history, safari_history, zsh_history, spotlight, markdown_vault, knowledgec and
approved_sqlite rows had no `evidence` row, and old claude_code / codex_cli / git_log rows (pre-D24)
lack one too, so get_evidence returned nothing for them. New ingest writes the locator itself
(see penpal.store.evidence for the formats); this pass backfills what already sits in the DB.

  * Locators are built from stored metadata only (raw_ref, sources.locator, metadata_json). No source
    file is opened, stat-ed or resolved: a wrong or stale path simply reads as "source unavailable" later.
  * knowledgec / approved_sqlite get "unresolvable://<source>" with available=0 (no openable original).
  * Where the stored metadata is not enough the row stays without evidence; the totals are logged.
  * Browser/zsh paths are not stored per row: they come from the `sources` table when it has exactly one
    entry for the plugin, else from the plugin's default location under the current home directory
    (the same default the plugin uses). Chrome additionally needs metadata.profile.
  * Idempotent: only rows without any evidence are considered, so a rerun inserts nothing.
    run_migrations wraps it in one transaction together with the version stamp.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from penpal.store.locators import browser_locator, split_git_locator, unresolvable_locator

LOGGER = logging.getLogger(__name__)
_BATCH = 2000
_SOURCES = (
    "claude_code", "codex_cli", "git_log", "zsh_history", "chrome_history", "safari_history",
    "spotlight", "markdown_vault", "knowledgec", "approved_sqlite",
)
_UNRESOLVABLE = frozenset({"knowledgec", "approved_sqlite"})
Locator = Optional[tuple[str, Optional[int], int]]  # (locator, line, available)


def _meta(raw: object) -> dict:
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _tail_int(text: str) -> Optional[int]:
    tail = text.rpartition(":")[2]
    return int(tail) if tail.isascii() and tail.isdigit() and 0 < len(tail) < 19 else None


def _is_abs(path: object) -> bool:
    return isinstance(path, str) and path.startswith("/") and "\x00" not in path


def _sole_locators(conn: sqlite3.Connection) -> dict[str, str]:
    """plugin -> its only registered sources.locator (plugins with several or none are omitted)."""
    found: dict[str, list[str]] = {}
    for kind, locator in conn.execute("SELECT type, locator FROM sources ORDER BY id"):
        found.setdefault(kind, []).append(locator)
    return {kind: locs[0] for kind, locs in found.items() if len(locs) == 1}


def _locate(source: str, raw_ref: str, meta: dict, ext: Optional[str], src_locator: Optional[str],
            sole: dict[str, str], home: Path) -> Locator:
    if source in _UNRESOLVABLE:
        return unresolvable_locator(source), None, 0

    if source == "zsh_history":
        line = _tail_int(raw_ref) if raw_ref.startswith("zsh:line:") else None
        path = sole.get("zsh_history") or str(home / ".zsh_history")
        return (path, line, 1) if line else None

    if source in ("chrome_history", "safari_history"):
        prefix = "chrome:visit:" if source == "chrome_history" else "safari:visit:"
        tail = raw_ref[len(prefix):] if raw_ref.startswith(prefix) else ""
        if not (tail.isascii() and tail.isdigit() and 0 < len(tail) < 19):
            return None
        if source == "safari_history":
            return browser_locator(str(home / "Library" / "Safari" / "History.db"), tail), None, 1
        profile = meta.get("profile")
        if not isinstance(profile, str) or not profile or "/" in profile or profile.startswith("."):
            return None
        db = home / "Library" / "Application Support" / "Google" / "Chrome" / profile / "History"
        return browser_locator(str(db), tail), None, 1

    if source == "spotlight":
        path = raw_ref[len("spotlight:"):] if raw_ref.startswith("spotlight:") else meta.get("kMDItemPath")
        return (path, None, 1) if _is_abs(path) else None

    if source == "markdown_vault":
        for key in ("path", "file_path"):
            if _is_abs(meta.get(key)):
                return meta[key], None, 1
        return None

    if source == "claude_code":
        # raw_ref = claude:projects:<project dir name>:<file>.jsonl:<line>
        head, _, line_text = raw_ref.rpartition(":")
        head, _, file_name = head.rpartition(":")
        line = int(line_text) if line_text.isdigit() and 0 < len(line_text) < 19 else None
        if line is None or not file_name.endswith(".jsonl") or "/" in file_name or not head.startswith("claude:projects:"):
            return None
        dir_name = head[len("claude:projects:"):]
        project_dir = src_locator or meta.get("project_dir")
        if not _is_abs(project_dir) or Path(project_dir).name != dir_name:
            return None
        return f"{project_dir.rstrip('/')}/{file_name}", line, 1

    if source == "codex_cli":
        if raw_ref.startswith("codex:history:"):
            line = _tail_int(raw_ref)
            return (src_locator, line, 1) if line and _is_abs(src_locator) else None
        if raw_ref.startswith("codex:sessions:"):
            rest, _, line_text = raw_ref[len("codex:sessions:"):].rpartition(":")
            line = int(line_text) if line_text.isdigit() and 0 < len(line_text) < 19 else None
            if line is None or not rest or rest.startswith("/") or ".." in rest.split("/") or not _is_abs(src_locator):
                return None
            return f"{src_locator.rstrip('/')}/{rest}", line, 1
        return None

    if source == "git_log":
        repo = src_locator if _is_abs(src_locator) else meta.get("repo_path")
        commit = ext or meta.get("full_hash") or meta.get("commit_hash")
        if not _is_abs(repo) or not isinstance(commit, str):
            return None
        locator = f"{repo}@{commit}"
        return (locator, None, 1) if split_git_locator(locator) is not None else None

    return None


def migrate_v28(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    home = Path.home()
    sole = _sole_locators(conn)
    now = datetime.now(timezone.utc).isoformat()
    marks = ",".join("?" * len(_SOURCES))
    stats: dict[str, list[int]] = {}
    last = 0
    while True:
        rows = conn.execute(
            "SELECT e.id, e.source, e.content_text, e.metadata_json, e.external_id, s.locator "
            "FROM raw_events e LEFT JOIN sources s ON s.id = e.source_id "
            f"WHERE e.event_type = 'semantic_event' AND e.source IN ({marks}) AND e.id > ? "
            "AND NOT EXISTS (SELECT 1 FROM evidence ev WHERE ev.event_id = e.id) "
            "ORDER BY e.id LIMIT ?",
            (*_SOURCES, last, _BATCH),
        ).fetchall()
        if not rows:
            break
        inserts = []
        for rid, source, text, meta_json, ext, src_locator in rows:
            meta = _meta(meta_json)
            raw_ref = meta.get("raw_ref")
            found = _locate(source, raw_ref if isinstance(raw_ref, str) else "", meta, ext, src_locator, sole, home)
            counts = stats.setdefault(source, [0, 0])
            if found is None:
                counts[1] += 1
                continue
            locator, line, available = found
            digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
            inserts.append((rid, locator, line, digest, available, now))
            counts[0] += 1
        if inserts:
            conn.executemany(
                "INSERT INTO evidence(event_id, locator, offset_or_line, content_hash, available, created_at) "
                "VALUES (?,?,?,?,?,?)",
                inserts,
            )
        last = rows[-1][0]
    created = sum(c[0] for c in stats.values())
    skipped = sum(c[1] for c in stats.values())
    LOGGER.info(
        "migration v28 evidence backfill: %d created, %d left without evidence (metadata not enough); %s",
        created, skipped, ", ".join(f"{k}={v[0]}/{v[1]}" for k, v in sorted(stats.items())) or "no candidate rows",
    )
    return {k: (v[0], v[1]) for k, v in stats.items()}
