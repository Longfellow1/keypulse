"""Read an evidence original back out (M2, issue #57) for the few sources where that is safe and useful.

  * chrome_history / safari_history  one visit row (title, URL, visit time), read-only. The History DB
    is opened in place read-only; if the browser holds a lock it is copied to a temp file and read there.
  * spotlight                        file facts (exists, size, mtime); text only for a plain-text /
    markdown file under a size cap, never a binary and never a path the cleaning rules exclude.

Everything else (claude_code, codex_cli, git_log, zsh_history, markdown_vault) stays location-only.
Returns None when the original (or the row inside it) can no longer be read. Output is untrusted text:
the caller runs it through the same query-time redaction as every other field.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from penpal.privacy.desensitizer import desensitize
from penpal.store.evidence import split_browser_locator

TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown"})
MAX_TEXT_FILE_BYTES = 64 * 1024
MAX_TEXT_CHARS = 2000
_WEBKIT_EPOCH_OFFSET_US = 11_644_473_600 * 1_000_000
_CF_EPOCH_OFFSET_S = 978_307_200

_CHROME_SQL = (
    "SELECT urls.url, urls.title, visits.visit_time FROM visits "
    "JOIN urls ON visits.url = urls.id WHERE visits.id = ?"
)
_SAFARI_SQL = (
    "SELECT hi.url, v.title, v.visit_time FROM history_visits v "
    "JOIN history_items hi ON v.history_item = hi.id WHERE v.id = ?"
)


def _query_one(db_path: str, sql: str, visit_id: int) -> Optional[tuple]:
    """Read-only lookup. A locked DB (browser running) falls back to a temp copy."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=0.2)
        try:
            return conn.execute(sql, (visit_id,)).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    try:
        with tempfile.TemporaryDirectory(prefix="kp-evidence-") as tmp:
            copy = os.path.join(tmp, "history.db")
            shutil.copy2(db_path, copy)
            conn = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            try:
                return conn.execute(sql, (visit_id,)).fetchone()
            finally:
                conn.close()
    except (sqlite3.Error, OSError):
        return None


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def _browser_original(source: str, locator: str) -> Optional[dict[str, Any]]:
    from penpal.sources.cleaning.file_whitelist import is_blocked_sqlite

    parsed = split_browser_locator(locator)
    if parsed is None or source not in ("chrome_history", "safari_history"):
        return None
    db_path, visit_id = parsed
    if not os.path.isfile(db_path) or is_blocked_sqlite(Path(db_path))[0]:
        return None
    row = _query_one(db_path, _CHROME_SQL if source == "chrome_history" else _SAFARI_SQL, visit_id)
    if row is None:
        return None
    url, title, visit_time = row
    visited_at: Optional[str] = None
    if isinstance(visit_time, (int, float)):
        try:
            if source == "chrome_history":
                visited_at = _iso(datetime.fromtimestamp((int(visit_time) - _WEBKIT_EPOCH_OFFSET_US) / 1_000_000, tz=timezone.utc))
            else:
                visited_at = _iso(datetime.fromtimestamp(float(visit_time) + _CF_EPOCH_OFFSET_S, tz=timezone.utc))
        except (OverflowError, OSError, ValueError):
            visited_at = None
    return {
        "kind": "browser_visit",
        "title": str(title or ""),
        "url": desensitize(str(url or "")),
        "visited_at": visited_at,
    }


def _file_original(locator: str) -> Optional[dict[str, Any]]:
    from penpal.sources.cleaning.path_filter import is_excluded_path

    path = Path(locator)
    try:
        st = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    out: dict[str, Any] = {
        "kind": "file",
        "exists": True,
        "size_bytes": st.st_size,
        "modified_at": _iso(datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)),
        "text": None,
    }
    if path.suffix.lower() in TEXT_SUFFIXES and st.st_size <= MAX_TEXT_FILE_BYTES and not is_excluded_path(path)[0]:
        try:
            data = path.read_bytes()[: MAX_TEXT_FILE_BYTES + 1]
        except OSError:
            return out
        if len(data) <= MAX_TEXT_FILE_BYTES and b"\x00" not in data:
            out["text"] = data.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]
    return out


def read_original(source: str, locator: str) -> Optional[dict[str, Any]]:
    """The original behind an evidence row, or None if it cannot be read any more. Only called for
    sources in ORIGINAL_SOURCES."""
    if source in ("chrome_history", "safari_history"):
        return _browser_original(source, locator)
    if source == "spotlight":
        return _file_original(locator)
    return None


ORIGINAL_SOURCES = frozenset({"chrome_history", "safari_history", "spotlight"})
