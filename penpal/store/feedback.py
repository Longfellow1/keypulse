"""Lightweight result feedback (M2-3 / #63): a ledger of what each client was actually shown, and the
correct / wrong / missing verdicts it files about those results.

The ledger is the authority for `rate_result`: an id is ratable only if this exact client was returned
it within RETURNED_TTL_DAYS and it is still inside that client's current grant scope. Anything else
(never seen, expired, other client's, out of scope, malformed, nonexistent) is indistinguishable.
Stored text (search keywords, note) is secret-redacted and length-capped before it is written.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from penpal.foundation import desensitizer
from penpal.store.db import get_conn

RETURNED_TTL_DAYS = 7
MAX_NOTE_CHARS = 500
MAX_QUERY_CHARS = 200
MAX_FEEDBACK_PER_CLIENT = 1000
VERDICTS = ("correct", "wrong", "missing")

_REF = re.compile(r"(evt|evd)_([0-9]{1,18})")


def clean_text(text: Optional[str], limit: int) -> Optional[str]:
    """Redact secrets / PII, collapse whitespace, cap length. None / blank -> None."""
    if not isinstance(text, str):
        return None
    redacted = desensitizer.redact_secrets(desensitizer.desensitize(text))
    collapsed = " ".join(redacted.split())
    return collapsed[:limit] or None


def parse_ref(raw: object) -> Optional[tuple[str, int]]:
    """'evt_12' -> ('evt', 12); anything else (including non-ASCII digits, signs, spaces) -> None."""
    if not isinstance(raw, str):
        return None
    m = _REF.fullmatch(raw)
    return (m.group(1), int(m.group(2))) if m else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cutoff() -> str:
    return (_now() - timedelta(days=RETURNED_TTL_DAYS)).isoformat()


def record_returned(client_id: str, items: Iterable[tuple[int, Optional[int]]], query: Optional[str]) -> None:
    """Remember that `client_id` was handed these (event_id, evidence_id) pairs. Best effort: a failure
    here never breaks the response (it only means a later rating of these ids is refused)."""
    rows = list(items)
    if not rows:
        return
    stored_query = clean_text(query, MAX_QUERY_CHARS)
    now = _now().isoformat()
    try:
        conn = get_conn()
        conn.execute("DELETE FROM mcp_returned_results WHERE returned_at < ?", (_cutoff(),))
        conn.executemany(
            "INSERT INTO mcp_returned_results(client_id, event_id, evidence_id, returned_at, query) VALUES (?,?,?,?,?)"
            " ON CONFLICT(client_id, event_id) DO UPDATE SET returned_at = excluded.returned_at,"
            " evidence_id = COALESCE(excluded.evidence_id, evidence_id), query = COALESCE(excluded.query, query)",
            [(client_id, ev, evd, now, stored_query) for ev, evd in rows],
        )
        conn.commit()
    except sqlite3.Error:
        pass


def lookup_returned(
    client_id: str, ref: object, *, buckets: Sequence[str], sources: Optional[Sequence[str]]
) -> Optional[dict]:
    """The ledger row for `ref` if this client was returned it (unexpired) and the event is still inside
    the client's current scope; otherwise None. One query, scope applied in the WHERE clause."""
    parsed = parse_ref(ref)
    if parsed is None or not buckets or (sources is not None and not sources):
        return None
    kind, number = parsed
    column = "r.event_id" if kind == "evt" else "r.evidence_id"
    where = [f"{column} = ?", "r.client_id = ?", "r.returned_at >= ?",
             "e.bucket IN (%s)" % ",".join("?" * len(buckets))]
    params: list = [number, client_id, _cutoff(), *buckets]
    if sources is not None:
        where.append("e.source IN (%s)" % ",".join("?" * len(sources)))
        params.extend(sources)
    try:
        row = get_conn().execute(
            "SELECT r.event_id, r.evidence_id, r.query FROM mcp_returned_results r"
            " JOIN raw_events e ON e.id = r.event_id WHERE " + " AND ".join(where) + " LIMIT 1",
            params,
        ).fetchone()
    except sqlite3.Error:
        return None
    return dict(row) if row else None


def save_feedback(
    client_id: str,
    verdict: str,
    *,
    buckets: Sequence[str],
    event_id: Optional[int] = None,
    evidence_id: Optional[int] = None,
    query: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    """Insert or update (latest wins) one verdict, in one transaction. False when the per-client cap is hit."""
    stored_query = clean_text(query, MAX_QUERY_CHARS)
    stored_note = clean_text(note, MAX_NOTE_CHARS)
    conn = get_conn()
    now = _now().isoformat()
    buckets_json = json.dumps(sorted(buckets))
    with conn:  # commit on success, roll back on error
        if event_id is not None:
            existing = conn.execute(
                "SELECT 1 FROM mcp_feedback WHERE client_id = ? AND event_id = ?", (client_id, event_id)).fetchone()
        else:
            existing = conn.execute(
                "SELECT 1 FROM mcp_feedback WHERE client_id = ? AND event_id IS NULL AND query IS ?",
                (client_id, stored_query)).fetchone()
        if existing is None:
            n = conn.execute("SELECT COUNT(*) FROM mcp_feedback WHERE client_id = ?", (client_id,)).fetchone()[0]
            if n >= MAX_FEEDBACK_PER_CLIENT:
                return False
        if event_id is not None:
            conn.execute(
                "INSERT INTO mcp_feedback(created_at, client_id, verdict, event_id, evidence_id, query, note, grant_buckets)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(client_id, event_id) WHERE event_id IS NOT NULL DO UPDATE SET"
                " created_at = excluded.created_at, verdict = excluded.verdict, evidence_id = excluded.evidence_id,"
                " query = excluded.query, note = excluded.note, grant_buckets = excluded.grant_buckets",
                (now, client_id, verdict, event_id, evidence_id, stored_query, stored_note, buckets_json),
            )
        else:
            conn.execute(
                "INSERT INTO mcp_feedback(created_at, client_id, verdict, event_id, evidence_id, query, note, grant_buckets)"
                " VALUES (?,?,?,NULL,NULL,?,?,?)"
                " ON CONFLICT(client_id, query) WHERE event_id IS NULL DO UPDATE SET"
                " created_at = excluded.created_at, verdict = excluded.verdict, note = excluded.note,"
                " grant_buckets = excluded.grant_buckets",
                (now, client_id, verdict, stored_query, stored_note, buckets_json),
            )
    return True


def feedback_rows(limit: Optional[int] = None) -> list[dict]:
    sql = "SELECT * FROM mcp_feedback ORDER BY id DESC"
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (max(1, int(limit)),)
    return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def feedback_counts() -> dict[str, int]:
    counts = {v: 0 for v in VERDICTS}
    for r in get_conn().execute("SELECT verdict, COUNT(*) AS n FROM mcp_feedback GROUP BY verdict"):
        counts[r["verdict"]] = r["n"]
    return counts
