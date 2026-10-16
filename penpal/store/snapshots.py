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

import difflib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

# Sources whose rows are progressive snapshots. keyboard_chunk is NOT one: its rows are disjoint
# buffered flushes (silence or 20 s), so no row extends the previous one.
SNAPSHOT_SOURCES = ("ax_text",)
SNAPSHOT_WINDOW = timedelta(seconds=120)
# Unconfirmed IME pinyin at the end of a field: lowercase ASCII letters, apostrophes and spaces.
_PINYIN_TAIL = re.compile(r"[a-z' ]+$")
_PINYIN_RUN = re.compile(r"[a-z' ]+")
_CJK_ANY = re.compile(r"[\u4e00-\u9fff]")
# The stem left after removing that tail must end in CJK text or CJK/fullwidth punctuation.
_CJK_END = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]$")
# A later state that edits the same draft in place (a word inserted mid-sentence) is near-identical.
_EDIT_SIMILARITY = 0.8


def _normalise(text: Optional[str]) -> str:
    return " ".join(str(text or "").lower().split())


def is_superseded_by(prev_text: Optional[str], next_text: Optional[str]) -> bool:
    """True when `prev_text` is an earlier state of `next_text`:
      1. it is a prefix of it, or its text without the last word is a prefix of it and the next text
         continues past that word;
      2. an unconfirmed pinyin tail (lowercase ASCII letters, apostrophes, spaces) is removed from
         `prev_text`, the remaining stem ends in CJK text or CJK punctuation, and `next_text` starts
         with that stem and is longer than it;
      3. the next text edits the same draft: similarity >= 0.8 and it is not shorter.
    Empty texts never supersede (an empty stem would match everything)."""
    prev = _normalise(prev_text)
    nxt = _normalise(next_text)
    if not prev or not nxt:
        return False
    if nxt.startswith(prev):
        return True
    stem = prev.rsplit(" ", 1)[0] if " " in prev else ""
    if stem and nxt.startswith(stem + " "):
        return True
    pinyin_stem = _PINYIN_TAIL.sub("", prev)
    if pinyin_stem != prev and _CJK_END.search(pinyin_stem) and len(nxt) > len(pinyin_stem) \
            and nxt.startswith(pinyin_stem):
        return True
    if len(nxt) >= len(prev) and difflib.SequenceMatcher(None, prev, nxt).ratio() >= _EDIT_SIMILARITY:
        return True
    # Pinyin inside the sentence turning into characters ("如dou ban di" -> "如豆包电脑版") shortens the
    # text; compare the drafts with lowercase pinyin/ASCII runs removed from both sides.
    prev_core, next_core = _PINYIN_RUN.sub("", prev), _PINYIN_RUN.sub("", nxt)
    return bool(prev_core) and _CJK_ANY.search(prev_core) is not None and len(next_core) >= len(prev_core) \
        and difflib.SequenceMatcher(None, prev_core, next_core).ratio() >= _EDIT_SIMILARITY


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
