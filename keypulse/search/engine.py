from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from keypulse.search.backends import resolve_search_backend
from keypulse.store.capture_events import CAPTURE_SEARCHABLE_EVENT_TYPES
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


def _types(event_type: "str | Iterable[str] | None") -> list[str]:
    if event_type is None:
        return []
    return [event_type] if isinstance(event_type, str) else list(event_type)


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _duration(start: Optional[str], end: Optional[str]) -> str:
    try:
        seconds = int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())
    except (TypeError, ValueError):
        return ""
    if seconds < 0:
        return ""
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def display_text(row: dict) -> str:
    """Text a Host sees for an event. Capture rows are labelled with their app (a window focus
    session is app | title | duration) so the Host knows what kind of record it is; other rows are
    just their content."""
    content = row.get("content_text") or ""
    if row.get("event_type") not in CAPTURE_SEARCHABLE_EVENT_TYPES:
        return content
    app = (row.get("app_name") or "").strip()
    if row.get("event_type") == "window_focus_session":
        parts = [app, (row.get("window_title") or "").strip(), _duration(row.get("occurred_at"), row.get("ts_end"))]
        return " | ".join(p for p in parts if p)
    return f"[{app}] {content}" if app else content


def _haystack(row) -> str:
    return " ".join(
        ((row["content_text"] or "") + " " + (row["window_title"] or "") + " " + (row["app_name"] or "")).lower().split()
    )


# Rows an AI host wrote after calling a KeyPulse MCP tool (paraphrase of KeyPulse's own output):
# kept in the DB, flagged by the source plugin (metadata.via_keypulse), hidden by default.
# Also hidden: keyboard chunks captured before the v24 tap fix (metadata.pre_fix_keyboard, set by v25).
_NOT_ECHO_SQL = (
    "(CASE WHEN json_valid(e.metadata_json) THEN json_extract(e.metadata_json, '$.via_keypulse') END) IS NOT 1"
    " AND (CASE WHEN json_valid(e.metadata_json)"
    " THEN json_extract(e.metadata_json, '$.pre_fix_keyboard') END) IS NOT 1"
)

_SCOPED_CANDIDATES = 500
_TF_SATURATION = 0.9   # bm25-style term-frequency saturation: repeats add less and less
_LEN_NORM = 0.9        # strong length normalisation: a long transcript message is not "more relevant"
_REF_LEN = 200         # fixed reference length in chars (a constant, not a corpus average)
_DENSITY_WEIGHT = 4.0  # share of the text that is the query's keywords: focused short rows win
_RARE_BOOST = 0.75     # exact-token hit of a rare keyword (hash, version, file name, issue key)
_DENSITY_MIN_LEN = 80  # a bare one-word row is not "dense", it is just short

_HEX_ID = re.compile(r"[0-9a-f]{7,40}")
_VERSION = re.compile(r"v?\d+(?:\.\d+)+(?:[-+][\w.]+)?")
_FILE_NAME = re.compile(r"[\w\-]+\.[a-z][a-z0-9]{0,5}")
_ISSUE_KEY = re.compile(r"[a-z][a-z0-9]*-\d+")

# "I copied it" cues: a keyword saying where the answer lives. Rows from that source rank first.
_SOURCE_CUES = {
    "clipboard": "clipboard", "copied": "clipboard", "copy": "clipboard", "pasted": "clipboard",
    "剪贴板": "clipboard", "复制": "clipboard", "拷贝": "clipboard", "粘贴": "clipboard",
}


def _is_rare_token(term: str) -> bool:
    """Single-token keywords that identify one thing: commit hash, version, file name, issue key."""
    t = term.lower()
    if " " in t or len(t) < 4:
        return False
    if _HEX_ID.fullmatch(t) and any(c.isdigit() for c in t):
        return True
    return bool(_VERSION.fullmatch(t) or _FILE_NAME.fullmatch(t) or _ISSUE_KEY.fullmatch(t))


def _cued_sources(terms: list[str]) -> set[str]:
    return {_SOURCE_CUES[t.lower()] for t in terms if t.lower() in _SOURCE_CUES}


def _has_token(text: str, token: str) -> bool:
    """Exact token match (not a substring of a longer word or hash)."""
    return re.search(r"(?<![\w])" + re.escape(token) + r"(?![\w])", text) is not None


def _doc_score(row, terms: list[str]) -> float:
    """Relevance from the document alone: term occurrences (saturating, length-normalised), distinct-term
    coverage, keyword density, and a small boost for exact rare-token hits. No document-frequency /
    corpus statistics."""
    text = _haystack(row)
    distinct = set(x.lower() for x in terms)
    norm = 1 - _LEN_NORM + _LEN_NORM * (len(text) / _REF_LEN)
    score = 0.0
    matched = 0
    matched_chars = 0
    for t in distinct:
        tf = text.count(t)
        if tf:
            matched += 1
            matched_chars += len(t)
            score += tf * (_TF_SATURATION + 1) / (tf + _TF_SATURATION * norm)
            if _is_rare_token(t) and _has_token(text, t):
                score += _RARE_BOOST
    score *= 1 + matched / max(1, len(distinct))
    return score + _DENSITY_WEIGHT * min(1.0, matched_chars / max(_DENSITY_MIN_LEN, len(text)))


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
    text = _haystack(row)
    return [t for t in terms if t.lower() in text]


def search_events(
    keywords: list[str],
    *,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    sources: Optional[list[str]] = None,
    buckets: Optional[list[str]] = None,
    event_type: "str | Iterable[str] | None" = "semantic_event",
    limit: int = 20,
    include_keypulse_echoes: bool = False,
) -> list[dict]:
    """Keyword search over raw_events: OR across keywords, ranked by how many distinct keywords a
    document matches (coverage), then a per-document score, then the source's semantic_weight,
    then recency. (Recency is a unique key, ts + id, so weight must sit before it to ever act.)
    A "copied"-style keyword ranks that source's rows (clipboard) first. Rows with identical text (same
    source family and bucket) are then folded into one hit with occurrences / first_seen / last_seen,
    before LIMIT; counts cover the (<= 500 newest) in-scope candidates.

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
        params.append(_like(t))
    for t in terms:  # the app is part of what a capture record is about ("how long in Figma")
        any_match.append("e.app_name LIKE ? ESCAPE '\\'")
        params.append(_like(t))
    where = ["(" + " OR ".join(any_match) + ")"]
    types = _types(event_type)
    if types:
        where.append("e.event_type IN (%s)" % ",".join("?" * len(types)))
        params.extend(types)
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
    if not include_keypulse_echoes:
        where.append(_NOT_ECHO_SQL)
    limit = max(1, min(int(limit), 100))
    params.append(_SCOPED_CANDIDATES)

    sql = f"""
        SELECT e.id, e.source, e.ts_start AS occurred_at, e.bucket, e.kind, e.content_text, e.window_title,
               e.app_name, e.event_type, e.ts_end, e.semantic_weight,
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
    cued = _cued_sources(terms)
    scored = []
    for r in rows:
        found = _matched(r, terms)
        if found:
            scored.append((
                1 if r["source"] in cued else 0, len(found), round(_doc_score(r, terms), 6),
                r["semantic_weight"] or 0.0, r, found,
            ))
    # Stable sort over a recency-ordered list (ts DESC, id DESC): source cue, coverage, document score,
    # semantic_weight, then recency. Deterministic for identical input.
    scored.sort(key=lambda x: x[:4], reverse=True)
    hits = []
    for r, found, occurrences, first_seen, last_seen in _collapse(scored)[:limit]:
        d = dict(r)
        d.pop("semantic_weight", None)
        d["snippet"] = _snippet(display_text(d), found)
        for k in ("content_text", "window_title", "app_name", "event_type", "ts_end"):
            d.pop(k, None)
        d["matched"] = len(found)
        d["keyword_count"] = len(terms)
        d["occurrences"] = occurrences
        d["first_seen"] = first_seen
        d["last_seen"] = last_seen
        hits.append(d)
    return hits


_SOURCE_FAMILY = {"chrome_history": "browser", "safari_history": "browser", "browser_url": "browser"}


def _collapse_key(r) -> tuple:
    text = " ".join(
        ((r["content_text"] or "") + " | " + (r["window_title"] or "") + " | " + (r["app_name"] or "")).lower().split()
    )
    return (_SOURCE_FAMILY.get(r["source"], r["source"]), r["bucket"], text)


def _collapse(scored: list) -> list:
    """Group ranked rows with the same normalised text (same source family and bucket) into one hit:
    the best-ranked row is the representative (its own evidence is kept) and the group carries
    occurrences / first_seen / last_seen. It only sees rows the WHERE clause already admitted (grant
    buckets and sources), and runs before LIMIT, so it can neither merge nor count anything outside
    the grant. Groups keep the rank order of their best member."""
    groups: dict[tuple, list] = {}
    for item in scored:
        groups.setdefault(_collapse_key(item[4]), []).append(item)
    out = []
    for members in groups.values():
        rep = members[0]
        times = [m[4]["occurred_at"] for m in members]
        out.append((rep[4], rep[5], len(members), min(times), max(times)))
    return out


TIMELINE_DEFAULT_LIMIT = 50
TIMELINE_MAX_LIMIT = 200


def timeline_events(
    *,
    since: Optional[str],
    until: Optional[str],
    sources: Optional[list[str]],
    buckets: list[str],
    after: Optional[tuple[str, int]] = None,
    event_type: "str | Iterable[str]" = "semantic_event",
    limit: int = TIMELINE_DEFAULT_LIMIT,
    include_keypulse_echoes: bool = False,
) -> tuple[list[dict], bool]:
    """Events in real occurred_at order (ascending, id as tiebreak), scope applied in the WHERE
    clause before LIMIT. `after` is a (occurred_at, id) keyset cursor. Returns (rows, has_more);
    has_more is computed from in-scope rows only (fetches limit + 1)."""
    limit = max(1, min(int(limit), TIMELINE_MAX_LIMIT))
    types = _types(event_type)
    if not buckets or not types or (sources is not None and not sources):
        return [], False
    where = ["e.event_type IN (%s)" % ",".join("?" * len(types)), "e.bucket IN (%s)" % ",".join("?" * len(buckets))]
    params: list = [*types, *buckets]
    if not include_keypulse_echoes:
        where.append(_NOT_ECHO_SQL)
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
        SELECT e.id, e.source, e.ts_start AS occurred_at, e.bucket, e.content_text, e.window_title,
               e.app_name, e.event_type, e.ts_end,
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
        d["snippet"] = _snippet(display_text(d), [], width=80)[:160]
        for k in ("content_text", "window_title", "app_name", "event_type", "ts_end"):
            d.pop(k, None)
        out.append(d)
    return out, has_more


def scoped_evidence(
    evidence_id: int,
    *,
    sources: Optional[list[str]],
    buckets: list[str],
    event_type: "str | Iterable[str]" = "semantic_event",
) -> Optional[dict]:
    """One evidence row joined to its event, only if the event is inside the scope (same WHERE
    rules as search). Out-of-scope and nonexistent ids both return None."""
    types = _types(event_type)
    if not buckets or not types or (sources is not None and not sources):
        return None
    where = ["ev.id = ?", "e.event_type IN (%s)" % ",".join("?" * len(types)),
             "e.bucket IN (%s)" % ",".join("?" * len(buckets))]
    params: list = [evidence_id, *types, *buckets]
    if sources is not None:
        where.append("e.source IN (%s)" % ",".join("?" * len(sources)))
        params.extend(sources)
    row = get_conn().execute(
        f"""
        SELECT ev.id AS evidence_id, ev.event_id, ev.locator, ev.offset_or_line AS line, ev.available AS stored_available,
               e.source, e.ts_start AS occurred_at, e.bucket, e.content_text, e.window_title, e.app_name,
               e.event_type, e.ts_end
        FROM evidence ev JOIN raw_events e ON e.id = ev.event_id
        WHERE {' AND '.join(where)}
        """,
        params,
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["excerpt_text"] = display_text(out)
    return out
