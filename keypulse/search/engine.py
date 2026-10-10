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


def _normalize_keywords(keywords: list[str]) -> list[str]:
    """Collapse whitespace, drop quotes/blanks, de-duplicate case-insensitively (order kept)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in keywords:
        k = " ".join(str(raw).replace('"', " ").split())
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def _matched(row, terms: list[str]) -> list[str]:
    """Distinct keywords that occur in this document (case-insensitive substring, document only)."""
    text = " ".join(((row["content_text"] or "") + " " + (row["window_title"] or "")).lower().split())
    return [t for t in terms if t.lower() in text]


def search_events(
    keywords: list[str],
    *,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    sources: Optional[list[str]] = None,
    buckets: Optional[list[str]] = None,
    event_type: Optional[str] = "semantic_event",
    limit: int = 20,
) -> list[dict]:
    """Keyword search over raw_events: OR across keywords, ranked by how many distinct keywords a
    document matches (coverage), then a per-document score, then recency.

    The caller (an AI host) is responsible for turning a question into keywords; no query
    understanding happens here. `since`/`until` bound the event's real occurred_at (ts_start),
    never the ingestion time. Returns [] for no usable keywords. Each hit carries `evidence_id`
    (first evidence row), `matched` (number of distinct keywords found) and `keyword_count`.

    `buckets` (None = unrestricted, [] = nothing) and `sources` are applied in the WHERE clause,
    before LIMIT, never as a post-filter. Ranking pulls the newest 500 in-scope candidates and
    ranks them with per-document features only; bm25 / IDF depend on corpus-wide statistics, so
    they would let out-of-scope data change in-scope order.
    """
    terms = _normalize_keywords(keywords)
    if not terms:
        return []
    since_ts = parse_time_bound(since)
    until_ts = parse_time_bound(until, end_of_day=True)

    long_terms = [t for t in terms if len(t) >= _MIN_FTS_CHARS]
    short_terms = [t for t in terms if len(t) < _MIN_FTS_CHARS]

    # Any keyword may match (OR). Long keywords go through the trigram index (a phrase when they
    # contain spaces); 1-2 char keywords cannot be indexed and fall back to LIKE.
    params: list = []
    any_match: list[str] = []
    if long_terms:
        any_match.append("e.id IN (SELECT rowid FROM raw_events_fts WHERE raw_events_fts MATCH ?)")
        params.append(" OR ".join('"%s"' % t for t in long_terms))
    for t in short_terms:
        any_match.append("e.content_text LIKE ? ESCAPE '\\'")
        params.append("%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    where = ["(" + " OR ".join(any_match) + ")"]
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
    params.append(_SCOPED_CANDIDATES)

    sql = f"""
        SELECT e.id, e.source, e.ts_start AS occurred_at, e.bucket, e.kind, e.content_text, e.window_title,
               (SELECT MIN(ev.id) FROM evidence ev WHERE ev.event_id = e.id) AS evidence_id,
               (SELECT ev.locator FROM evidence ev WHERE ev.event_id = e.id ORDER BY ev.id LIMIT 1) AS evidence_locator,
               (SELECT ev.offset_or_line FROM evidence ev WHERE ev.event_id = e.id ORDER BY ev.id LIMIT 1) AS evidence_line,
               (SELECT ev.available FROM evidence ev WHERE ev.event_id = e.id ORDER BY ev.id LIMIT 1) AS evidence_stored_available
        FROM raw_events e
        WHERE {' AND '.join(where)}
        ORDER BY e.ts_start DESC, e.id DESC
        LIMIT ?
    """
    rows = get_conn().execute(sql, params).fetchall()
    scored = []
    for r in rows:
        found = _matched(r, terms)
        if found:
            scored.append((len(found), _doc_score(r, terms), r, found))
    # Stable sort over a recency-ordered list: coverage, then document score, then recency.
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    hits = []
    for coverage, _score, r, found in scored[:limit]:
        d = dict(r)
        d.pop("window_title", None)
        d["snippet"] = _snippet(d.pop("content_text") or "", found)
        d["matched"] = coverage
        d["keyword_count"] = len(terms)
        hits.append(d)
    return hits


TIMELINE_DEFAULT_LIMIT = 50
TIMELINE_MAX_LIMIT = 200


def timeline_events(
    *,
    since: Optional[str],
    until: Optional[str],
    sources: Optional[list[str]],
    buckets: list[str],
    after: Optional[tuple[str, int]] = None,
    event_type: str = "semantic_event",
    limit: int = TIMELINE_DEFAULT_LIMIT,
) -> tuple[list[dict], bool]:
    """Events in real occurred_at order (ascending, id as tiebreak), scope applied in the WHERE
    clause before LIMIT. `after` is a (occurred_at, id) keyset cursor. Returns (rows, has_more);
    has_more is computed from in-scope rows only (fetches limit + 1)."""
    limit = max(1, min(int(limit), TIMELINE_MAX_LIMIT))
    if not buckets or (sources is not None and not sources):
        return [], False
    where = ["e.event_type = ?", "e.bucket IN (%s)" % ",".join("?" * len(buckets))]
    params: list = [event_type, *buckets]
    if sources is not None:
        where.append("e.source IN (%s)" % ",".join("?" * len(sources)))
        params.extend(sources)
    if since:
        where.append("e.ts_start >= ?")
        params.append(since)
    if until:
        where.append("e.ts_start <= ?")
        params.append(until)
    if after is not None:
        where.append("(e.ts_start > ? OR (e.ts_start = ? AND e.id > ?))")
        params.extend([after[0], after[0], after[1]])
    params.append(limit + 1)
    rows = get_conn().execute(
        f"""
        SELECT e.id, e.source, e.ts_start AS occurred_at, e.bucket, e.content_text,
               (SELECT MIN(ev.id) FROM evidence ev WHERE ev.event_id = e.id) AS evidence_id
        FROM raw_events e
        WHERE {' AND '.join(where)}
        ORDER BY e.ts_start ASC, e.id ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    has_more = len(rows) > limit
    out = []
    for r in rows[:limit]:
        d = dict(r)
        d["snippet"] = _snippet(d.pop("content_text") or "", [], width=80)[:160]
        out.append(d)
    return out, has_more


def scoped_evidence(
    evidence_id: int,
    *,
    sources: Optional[list[str]],
    buckets: list[str],
    event_type: str = "semantic_event",
) -> Optional[dict]:
    """One evidence row joined to its event, only if the event is inside the scope (same WHERE
    rules as search). Out-of-scope and nonexistent ids both return None."""
    if not buckets or (sources is not None and not sources):
        return None
    where = ["ev.id = ?", "e.event_type = ?", "e.bucket IN (%s)" % ",".join("?" * len(buckets))]
    params: list = [evidence_id, event_type, *buckets]
    if sources is not None:
        where.append("e.source IN (%s)" % ",".join("?" * len(sources)))
        params.extend(sources)
    row = get_conn().execute(
        f"""
        SELECT ev.id AS evidence_id, ev.event_id, ev.locator, ev.offset_or_line AS line, ev.available AS stored_available,
               e.source, e.ts_start AS occurred_at, e.bucket, e.content_text
        FROM evidence ev JOIN raw_events e ON e.id = ev.event_id
        WHERE {' AND '.join(where)}
        """,
        params,
    ).fetchone()
    return dict(row) if row else None
