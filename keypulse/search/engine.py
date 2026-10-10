from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from keypulse.search.backends import resolve_search_backend
from keypulse.store.db import get_conn


def search(
    query: str,
    app_name: Optional[str] = None,
    since: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    backend = resolve_search_backend()
    return backend.search(query, app_name=app_name, since=since, source=source, limit=limit)


def recent_clipboard(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM search_docs WHERE ref_type='clipboard' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_manual(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM search_docs WHERE ref_type='manual' ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_sessions_docs(limit: int = 20) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── event search (v20): FTS over raw_events, filtered by occurred_at ──────────

_MIN_FTS_CHARS = 3  # trigram tokenizer cannot index shorter terms


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def parse_time_bound(raw: str | datetime | None, *, end_of_day: bool = False) -> Optional[str]:
    """Normalise a user bound (ISO date / datetime / datetime obj) to UTC ISO.

    Raises ValueError on garbage; naive values are taken as UTC.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return _utc_iso(raw)
    text = str(raw).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if end_of_day and len(text) == 10:
        parsed = parsed + timedelta(days=1) - timedelta(microseconds=1)
    return _utc_iso(parsed)


def _snippet(text: str, terms: list[str], width: int = 80) -> str:
    text = " ".join((text or "").split())
    lowered = text.lower()
    pos = -1
    for term in terms:
        pos = lowered.find(term.lower())
        if pos >= 0:
            break
    if pos < 0:
        return text[: width * 2]
    start = max(0, pos - width)
    end = min(len(text), pos + width + 40)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


_SCOPED_CANDIDATES = 500
_TF_SATURATION = 1.2   # bm25-style term-frequency saturation
_LEN_NORM = 0.75       # strength of length normalisation
_REF_LEN = 200         # fixed reference length in chars (a constant, not a corpus average)


def _doc_score(row, terms: list[str]) -> float:
    """Relevance from the document alone: term occurrences (saturating), distinct-term coverage,
    length normalisation. No document-frequency / corpus statistics."""
    text = " ".join(((row["content_text"] or "") + " " + (row["window_title"] or "")).lower().split())
    norm = 1 - _LEN_NORM + _LEN_NORM * (len(text) / _REF_LEN)
    score = 0.0
    matched = 0
    for t in set(x.lower() for x in terms):
        tf = text.count(t)
        if tf:
            matched += 1
            score += tf * (_TF_SATURATION + 1) / (tf + _TF_SATURATION * norm)
    return score * (1 + matched / max(1, len(set(terms))))


def search_events(
    query: str,
    *,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    sources: Optional[list[str]] = None,
    buckets: Optional[list[str]] = None,
    event_type: Optional[str] = "semantic_event",
    limit: int = 20,
) -> list[dict]:
    """Full-text search over raw_events.

    `since`/`until` bound the event's real occurred_at (ts_start), never the
    ingestion time. Returns [] for empty queries. Each hit carries the
    `evidence_id` (first evidence row) and the terms that matched.

    `buckets` (None = unrestricted, [] = nothing) and `sources` are applied in
    the WHERE clause, before LIMIT, never as a post-filter. A bucket-scoped
    search ranks the newest 500 in-scope candidates by a per-document score
    (`_doc_score`), recency breaking ties; bm25 depends on corpus-wide
    statistics, so it would let out-of-scope data change in-scope order.
    """
    terms = [t for t in re.split(r"\s+", (query or "").replace('"', " ").strip()) if t]
    if not terms:
        return []
    since_ts = parse_time_bound(since)
    until_ts = parse_time_bound(until, end_of_day=True)

    long_terms = [t for t in terms if len(t) >= _MIN_FTS_CHARS]
    short_terms = [t for t in terms if len(t) < _MIN_FTS_CHARS]

    params: list = []
    if long_terms:
        match = " AND ".join('"%s"' % t for t in long_terms)
        from_sql = "raw_events e JOIN raw_events_fts ON raw_events_fts.rowid = e.id"
        where = ["raw_events_fts MATCH ?"]
        params.append(match)
        order = "bm25(raw_events_fts), e.ts_start DESC" if buckets is None else "e.ts_start DESC, e.id DESC"
    else:
        from_sql = "raw_events e"
        where = []
        order = "e.ts_start DESC, e.id DESC"
    for t in short_terms:
        where.append("e.content_text LIKE ? ESCAPE '\\'")
        params.append("%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if event_type:
        where.append("e.event_type = ?")
        params.append(event_type)
    if since_ts:
        where.append("e.ts_start >= ?")
        params.append(since_ts)
    if until_ts:
        where.append("e.ts_start <= ?")
        params.append(until_ts)
    if sources is not None:
        if not sources:
            return []
        where.append("e.source IN (%s)" % ",".join("?" * len(sources)))
        params.extend(sources)
    if buckets is not None:
        if not buckets:
            return []
        where.append("e.bucket IN (%s)" % ",".join("?" * len(buckets)))
        params.extend(buckets)
    limit = max(1, min(int(limit), 100))
    # Scoped search: pull a recency-ordered candidate window (scope filter in SQL), then rank it
    # in Python with per-document features only. Anything corpus-wide (bm25, IDF, avg length)
    # would let out-of-scope data change in-scope order.
    params.append(_SCOPED_CANDIDATES if buckets is not None else limit)

    sql = f"""
        SELECT e.id, e.source, e.ts_start AS occurred_at, e.bucket, e.kind, e.content_text, e.window_title,
               (SELECT MIN(ev.id) FROM evidence ev WHERE ev.event_id = e.id) AS evidence_id
        FROM {from_sql}
        WHERE {' AND '.join(where) or '1'}
        ORDER BY {order}
        LIMIT ?
    """
    rows = get_conn().execute(sql, params).fetchall()
    if buckets is not None:
        rows = sorted(rows, key=lambda r: _doc_score(r, terms), reverse=True)[:limit]  # stable: recency breaks ties
    hits = []
    for r in rows:
        d = dict(r)
        d.pop("window_title", None)
        d["snippet"] = _snippet(d.pop("content_text") or "", terms)
        d["matched_terms"] = terms
        hits.append(d)
    return hits
