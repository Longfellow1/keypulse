"""Progressive-typing snapshots: an earlier state of a text field that the next capture extends.

The ax_text watcher (opt-in, ax_text = false by default) emits a row every time the focused field's
text changes, so one sentence typed in a field is stored as many rows, each a prefix or extension of
the previous one (some end with IME pinyin fragments). Only the last, most complete state is a real
memory. Such a row is marked `metadata_json.superseded_by = <id of the next row>`: reversible,
nothing is deleted, search and timeline hide it, get_evidence still resolves it.

Used by migration v33 (existing rows) and by insert_raw_event (rows captured from now on), so both
paths compute the same marking.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

# Sources whose rows are progressive snapshots. keyboard_chunk is NOT one: its rows are disjoint
# buffered flushes (silence or 20 s), so no row extends the previous one.
SNAPSHOT_SOURCES = ("ax_text",)
SNAPSHOT_WINDOW = timedelta(seconds=120)


def _normalise(text: Optional[str]) -> str:
    return " ".join(str(text or "").lower().split())


def is_superseded_by(prev_text: Optional[str], next_text: Optional[str]) -> bool:
    """True when `prev_text` is an earlier state of `next_text`: either a prefix of it, or its text
    without the last token (a trailing IME fragment) is a prefix of it and the next text continues
    past that stem. Empty texts never supersede (an empty stem would match everything)."""
    prev = _normalise(prev_text)
    nxt = _normalise(next_text)
    if not prev or not nxt:
        return False
    if nxt.startswith(prev):
        return True
    stem = prev.rsplit(" ", 1)[0] if " " in prev else ""
    return bool(stem) and nxt.startswith(stem + " ")


def _parse(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def within_window(prev_ts: str, next_ts: str) -> bool:
    delta = _parse(next_ts) - _parse(prev_ts)
    return timedelta(0) <= delta <= SNAPSHOT_WINDOW


_MARK_SQL = (
    "UPDATE raw_events SET metadata_json = json_set("
    "CASE WHEN json_valid(metadata_json) THEN metadata_json ELSE '{}' END, '$.superseded_by', ?) "
    "WHERE id = ?"
)


def mark_superseded(conn: sqlite3.Connection, prev_id: int, next_id: int) -> None:
    conn.execute(_MARK_SQL, (next_id, prev_id))


def mark_previous_snapshot(conn: sqlite3.Connection, new_id: int, source: str, app_name: Optional[str],
                           ts_start: str, text: Optional[str]) -> None:
    """Capture-time marking: the newest earlier row of the same source and app is superseded by the
    new row when it qualifies. Called right after the new row is inserted."""
    if source not in SNAPSHOT_SOURCES or not text:
        return
    prev = conn.execute(
        "SELECT id, ts_start, content_text FROM raw_events "
        "WHERE source = ? AND app_name IS ? AND id <> ? AND ts_start <= ? "
        "AND content_text IS NOT NULL AND content_text <> '' "
        "ORDER BY ts_start DESC, id DESC LIMIT 1",
        (source, app_name, new_id, ts_start),
    ).fetchone()
    if prev is None:
        return
    if within_window(prev[1], ts_start) and is_superseded_by(prev[2], text):
        mark_superseded(conn, prev[0], new_id)
