"""
LLM-trigger decision module: fail-closed gateway for kernel's LLM calls.
Pure module with no KeyPulse-specific imports—only stdlib (sqlite3, datetime, pathlib, typing).
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple


def should_trigger(kind: str, *, now: datetime, db_path: Path, cfg: dict) -> Tuple[bool, str]:
    """
    Return (allowed, reason). Fail-closed: on any exception, return (False, 'error:<msg>').
    kind is one of: "T1", "T2", "T3".
    """
    try:
        if kind not in ("T1", "T2", "T3"):
            return False, "error:unknown_kind"

        if kind == "T2":
            return True, "T2:always_allowed"

        if kind == "T1":
            if not _has_activity_last(db_path, timedelta(hours=5), now):
                return False, "T1:no_activity_5h"
            return True, "T1:activity_ok"

        if kind == "T3":
            count_1h = _trigger_count_in(db_path, "T3", timedelta(hours=1), now)
            if count_1h >= 1:
                return False, "T3:global_cap_1h"

            count_5h = _trigger_count_in(db_path, "T3", timedelta(hours=5), now)
            if count_5h >= 3:
                return False, "T3:cap_5h"

            return True, "T3:allowed"

    except Exception as e:
        return False, f"error:{str(e)}"

    return False, "error:unreachable"


def _ensure_trigger_table(cursor: "sqlite3.Cursor") -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_trigger_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            outcome TEXT NOT NULL,
            note TEXT DEFAULT ''
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_llm_trigger_log_kind_ts
        ON llm_trigger_log(kind, ts_utc)
        """
    )


def record_trigger(
    kind: str,
    *,
    now: datetime,
    db_path: Path,
    outcome: str,
    note: str = "",
    pending_id: "int | None" = None,
) -> None:
    """
    Log a trigger decision/outcome.
    outcome is 'allowed' | 'skipped:<reason>' | 'ran:ok' | 'ran:fail'.
    If pending_id is given, UPDATE the existing row instead of inserting.
    Creates table on first call; idempotent.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        _ensure_trigger_table(cursor)

        if pending_id is not None:
            cursor.execute(
                "UPDATE llm_trigger_log SET outcome=?, note=? WHERE id=?",
                (outcome, note, pending_id),
            )
        else:
            cursor.execute(
                "INSERT INTO llm_trigger_log (kind, ts_utc, outcome, note) VALUES (?, ?, ?, ?)",
                (kind, now.isoformat(), outcome, note),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def record_trigger_pending(
    kind: str,
    *,
    now: datetime,
    db_path: Path,
    note: str = "",
) -> int:
    """
    INSERT a row with outcome='pending' and return its row id.
    Returns -1 on failure.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        _ensure_trigger_table(cursor)
        cursor.execute(
            "INSERT INTO llm_trigger_log (kind, ts_utc, outcome, note) VALUES (?, ?, 'pending', ?)",
            (kind, now.isoformat(), note),
        )
        row_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return row_id if row_id is not None else -1
    except Exception:
        return -1


def finalize_stale_pending(
    db_path: Path,
    *,
    now: datetime,
    timeout_min: int = 10,
) -> int:
    """
    Find rows with outcome='pending' older than timeout_min minutes and mark
    them as 'ran:crashed'. Returns the number of rows updated.
    """
    try:
        cutoff = (now - timedelta(minutes=timeout_min)).isoformat()
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        _ensure_trigger_table(cursor)
        cursor.execute(
            """
            UPDATE llm_trigger_log
            SET outcome='ran:crashed'
            WHERE outcome='pending' AND ts_utc < ?
            """,
            (cutoff,),
        )
        updated = cursor.rowcount
        conn.commit()
        conn.close()
        return updated
    except Exception:
        return 0


def _has_activity_last(
    db_path: Path, window: timedelta, now: datetime, min_chars: int = 50
) -> bool:
    """
    Count chars in raw_events.content_text where speaker='user' within the window.
    Return True if total >= min_chars. Fail-closed: False on any error.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cutoff = (now - window).isoformat()
        cursor.execute(
            """
            SELECT SUM(LENGTH(content_text)) as total_chars
            FROM raw_events
            WHERE speaker = 'user'
              AND ts_start > ?
            """,
            (cutoff,),
        )
        result = cursor.fetchone()
        conn.close()

        total = result[0] if result and result[0] else 0
        return total >= min_chars
    except Exception:
        return False


def _trigger_count_in(
    db_path: Path, kind: str, window: timedelta, now: datetime
) -> int:
    """
    Count rows in llm_trigger_log where kind matches and ts_utc is within window.
    """
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cutoff = (now - window).isoformat()
        cursor.execute(
            """
            SELECT COUNT(*) FROM llm_trigger_log
            WHERE kind = ? AND ts_utc > ?
            """,
            (kind, cutoff),
        )
        result = cursor.fetchone()
        conn.close()

        return result[0] if result else 0
    except Exception:
        return 0
