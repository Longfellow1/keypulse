"""Prepare a run: consistent DB copy, remove post-authoring transcripts, contamination scan."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from evals.realdata.questions import Question

TRANSCRIPT_SOURCES = ("claude_code", "codex_cli")


def backup_copy(src: Path, dest: Path) -> None:
    """Consistent copy via the sqlite3 backup API (safe while the daemon is writing)."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dest)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def strip_transcripts(db: Path, exclude_sessions: list[str], authored_after: str | None) -> dict:
    """Delete transcript rows of the given sessions and/or with ts_start >= authored_after.

    Python's sqlite3 is required: the FTS5 sync triggers need the FTS5 module.
    A session id matches if it appears in session_id, external_id, metadata_json or evidence locator.
    """
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    ph = ",".join("?" * len(TRANSCRIPT_SOURCES))
    deleted = {"sessions": 0, "after": 0}
    try:
        for sid in exclude_sessions:
            like = f"%{sid}%"
            cur = conn.execute(
                f"""DELETE FROM raw_events WHERE source IN ({ph}) AND (
                      session_id LIKE ? OR external_id LIKE ? OR metadata_json LIKE ?
                      OR id IN (SELECT event_id FROM evidence WHERE locator LIKE ?))""",
                (*TRANSCRIPT_SOURCES, like, like, like, like))
            deleted["sessions"] += cur.rowcount
        if authored_after:
            cur = conn.execute(f"DELETE FROM raw_events WHERE source IN ({ph}) AND ts_start >= ?",
                               (*TRANSCRIPT_SOURCES, authored_after))
            deleted["after"] = cur.rowcount
        conn.commit()
        conn.execute("VACUUM")  # deleted text must not linger in free pages
    finally:
        conn.close()
    return deleted


def contamination_counts(db: Path, questions: list[Question]) -> dict[str, int]:
    """Per question: number of remaining transcript rows containing a gold string. Counts only."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ph = ",".join("?" * len(TRANSCRIPT_SOURCES))
    out: dict[str, int] = {}
    try:
        for q in questions:
            needles = [n for n in (q.gold_match or (q.gold_answer,)) if n.strip()]
            ids: set[int] = set()
            for n in needles:
                rows = conn.execute(
                    f"SELECT id FROM raw_events WHERE source IN ({ph}) AND instr(lower(content_text), lower(?)) > 0",
                    (*TRANSCRIPT_SOURCES, n)).fetchall()
                ids.update(r[0] for r in rows)
            out[q.id] = len(ids)
    finally:
        conn.close()
    return out
