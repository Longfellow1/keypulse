"""Gold validation: are a question's gold rows really in the run DB, and do they contain the gold text?

Checks, for every question with expect answer or must_not_see (not_found has no gold rows):
- each gold_event_id exists in raw_events of the run DB copy (ids may be "123" or "evt_123");
- each gold_match string occurs (case-insensitive substring) in content_text or window_title of a gold row.
Warnings (do not fail): no gold_match; no gold_event_ids for an answer question.

Output carries only question ids, row ids and reason codes. Row text is never printed (real data).
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from evals.realdata.questions import Question

CHECKED_EXPECTS = ("answer", "must_not_see")
_ID_RE = re.compile(r"(?:evt_)?(\d+)")


@dataclass
class GoldCheck:
    qid: str
    status: str  # PASS | WARN | FAIL
    reasons: list[str] = field(default_factory=list)  # reason codes; ids and #indexes only, never text


def _fetch_rows(conn: sqlite3.Connection, ids: set[int]) -> dict[int, tuple[str, str]]:
    ph = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT id, content_text, window_title FROM raw_events WHERE id IN ({ph})",
                        tuple(ids)).fetchall()
    return {r[0]: (r[1] or "", r[2] or "") for r in rows}


def _check(q: Question, rows_by_id: dict[int, tuple[str, str]] | None) -> GoldCheck:
    """Pure check of one question against the rows it points to (rows_by_id may be None for no DB)."""
    fails: list[str] = []
    warns: list[str] = []
    ids: list[int] = []
    for i, raw in enumerate(q.gold_event_ids):
        m = _ID_RE.fullmatch(raw.strip())
        if m:
            ids.append(int(m.group(1)))
        else:
            fails.append(f"bad_event_id_format:#{i}")
    found = rows_by_id or {}
    for n in sorted(set(ids)):
        if n not in found:
            fails.append(f"missing_event_id:{n}")
    needles = [m.strip() for m in q.gold_match if m.strip()]
    if not needles:
        warns.append("no_gold_match")
    elif found:
        for j, needle in enumerate(q.gold_match):
            needle = needle.strip()
            if not needle:
                continue
            low = needle.lower()
            if not any(low in text.lower() or low in title.lower() for text, title in found.values()):
                fails.append(f"gold_match_not_in_gold_rows:#{j}")
    elif not q.gold_event_ids:
        warns.append("gold_match_unchecked_no_gold_rows")
    # malformed or missing ids already FAIL above, so gold_match is not re-reported for them
    if q.expect == "answer" and not q.gold_event_ids:
        warns.append("no_gold_event_ids")
    status = "FAIL" if fails else "WARN" if warns else "PASS"
    return GoldCheck(q.id, status, fails + warns)


def validate_gold(questions: list[Question], db: Path) -> list[GoldCheck]:
    """Check every answer / must_not_see question against the run DB copy. Read-only."""
    if not Path(db).exists():
        raise FileNotFoundError(f"run DB not found: {db}")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: list[GoldCheck] = []
    try:
        for q in questions:
            if q.expect not in CHECKED_EXPECTS:
                continue
            ids = {int(m.group(1)) for e in q.gold_event_ids if (m := _ID_RE.fullmatch(e.strip()))}
            rows = _fetch_rows(conn, ids) if ids else {}
            out.append(_check(q, rows))
    finally:
        conn.close()
    return out


def format_lines(results: list[GoldCheck], *, problems_only: bool = False) -> list[str]:
    lines = [f"{r.qid} {r.status}" + (f" {' '.join(r.reasons)}" if r.reasons else "")
             for r in results if not (problems_only and r.status == "PASS")]
    lines.append(summary_line(results))
    return lines


def summary_line(results: list[GoldCheck]) -> str:
    count = {s: sum(1 for r in results if r.status == s) for s in ("PASS", "WARN", "FAIL")}
    return (f"gold validation: {len(results)} checked, {count['PASS']} pass, {count['WARN']} warn, "
            f"{count['FAIL']} fail")


def has_failures(results: list[GoldCheck]) -> bool:
    return any(r.status == "FAIL" for r in results)
