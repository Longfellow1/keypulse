from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from penpal.capture.watchers.browser import DEFAULT_SUPPORTED_BROWSERS
from penpal.search.backends import resolve_search_backend
from penpal.store.capture_events import CAPTURE_SEARCHABLE_EVENT_TYPES
from penpal.store.context_label import CONTEXT_LABEL_SOURCES
from penpal.store.db import get_conn


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


def _haystack(row, *, with_label: bool = True) -> str:
    """Text a keyword is matched against. `with_label=False` leaves out the context label of imported
    rows, so scoring stays exactly what it was before labels existed (see _doc_score)."""
    title = row["window_title"] or ""
    if not with_label and row["source"] in CONTEXT_LABEL_SOURCES:
        title = ""
    return " ".join(((row["content_text"] or "") + " " + title + " " + (row["app_name"] or "")).lower().split())


# Rows an AI host wrote after calling a Penpal MCP tool (paraphrase of Penpal's own output):
# kept in the DB, flagged by the source plugin (metadata.via_penpal), hidden by default.
# Also hidden: keyboard chunks captured before the v24 tap fix (metadata.pre_fix_keyboard, set by v25).
_NOT_ECHO_SQL = (
    "(CASE WHEN json_valid(e.metadata_json) THEN json_extract(e.metadata_json, '$.via_penpal') END) IS NOT 1"
    " AND (CASE WHEN json_valid(e.metadata_json) THEN json_extract(e.metadata_json, '$.via_keypulse') END) IS NOT 1"
    " AND (CASE WHEN json_valid(e.metadata_json)"
    " THEN json_extract(e.metadata_json, '$.pre_fix_keyboard') END) IS NOT 1"
)

# Progressive-typing snapshots (ax_text): an earlier state of a sentence that a later row extends.
# Marked by migration v33 / store.snapshots (metadata_json.superseded_by). Always hidden from search
# and timeline; get_evidence still resolves them.
_NOT_SUPERSEDED_SQL = (
    "(CASE WHEN json_valid(e.metadata_json) THEN json_extract(e.metadata_json, '$.superseded_by') END) IS NULL"
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

def _is_rare_token(term: str) -> bool:
    """Single-token keywords that identify one thing: commit hash, version, file name, issue key."""
    t = term.lower()
    if " " in t or len(t) < 4:
        return False
    if _HEX_ID.fullmatch(t) and any(c.isdigit() for c in t):
        return True
    return bool(_VERSION.fullmatch(t) or _FILE_NAME.fullmatch(t) or _ISSUE_KEY.fullmatch(t))


def _has_token(text: str, token: str) -> bool:
    """Exact token match (not a substring of a longer word or hash)."""
    return re.search(r"(?<![\w])" + re.escape(token) + r"(?![\w])", text) is not None


def _doc_score(row, terms: list[str]) -> float:
    """Relevance from the document alone: term occurrences (saturating, length-normalised), distinct-term
    coverage, keyword density, and a small boost for exact rare-token hits. No document-frequency /
    corpus statistics."""
    text = _haystack(row, with_label=False)
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


def normalize_keywords(keywords: list[str]) -> list[str]:
    """Public form of the keyword normalisation search_events applies (the feedback uses the same list)."""
    return _normalize_keywords(keywords)


def _match_clause(terms: list[str]) -> tuple[str, list]:
    """SQL that is true for a row matching ANY of the normalised keywords (same predicate for search
    and for the per-keyword scope check)."""
    params: list = []
    any_match: list[str] = []
    long_terms = [t for t in terms if len(t) >= _MIN_FTS_CHARS]
    short_terms = [t for t in terms if len(t) < _MIN_FTS_CHARS]
    # Long keywords go through the trigram index (a phrase when they contain spaces); 1-2 char
    # keywords cannot be indexed and fall back to LIKE.
    if long_terms:
        any_match.append("e.id IN (SELECT rowid FROM raw_events_fts WHERE raw_events_fts MATCH ?)")
        params.append(" OR ".join('"%s"' % t for t in long_terms))
    for t in short_terms:
        any_match.append("e.content_text LIKE ? ESCAPE '\\'")
        params.append(_like(t))
    for t in terms:  # the app is part of what a capture record is about ("how long in Figma")
        any_match.append("e.app_name LIKE ? ESCAPE '\\'")
        params.append(_like(t))
    return "(" + " OR ".join(any_match) + ")", params


def _scope_clauses(
    *,
    since_ts: Optional[str],
    until_ts: Optional[str],
    sources: Optional[list[str]],
    buckets: Optional[list[str]],
    event_type: "str | Iterable[str] | None",
    include_penpal_echoes: bool,
) -> Optional[tuple[list[str], list]]:
    """WHERE clauses for the scope (event type, time bounds, sources, buckets, echo filter), or None
    when the scope is empty and nothing can match. Shared by search_events and keyword_in_scope."""
    where: list[str] = []
    params: list = []
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
            return None
        where.append("e.source IN (%s)" % ",".join("?" * len(sources)))
        params.extend(sources)
    if buckets is not None:
        if not buckets:
            return None
        where.append("e.bucket IN (%s)" % ",".join("?" * len(buckets)))
        params.extend(buckets)
    if not include_penpal_echoes:
        where.append(_NOT_ECHO_SQL)
    where.append(_NOT_SUPERSEDED_SQL)
    return where, params


def keyword_in_scope(
    keyword: str,
    *,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    sources: Optional[list[str]] = None,
    buckets: Optional[list[str]] = None,
    event_type: "str | Iterable[str] | None" = "semantic_event",
    include_penpal_echoes: bool = False,
) -> bool:
    """True when ANY row inside the scope matches this one keyword (same predicate and same scope
    filters as search_events). One EXISTS / LIMIT 1 query: it does not look at the 500-row candidate
    window, so 'no' here means no in-scope row at all. Out-of-scope rows never count."""
    terms = _normalize_keywords([keyword])
    if not terms:
        return False
    scope = _scope_clauses(
        since_ts=parse_time_bound(since), until_ts=parse_time_bound(until, end_of_day=True), sources=sources,
        buckets=buckets, event_type=event_type, include_penpal_echoes=include_penpal_echoes)
    if scope is None:
        return False
    scope_where, scope_params = scope
    match_sql, match_params = _match_clause(terms)
    sql = "SELECT 1 FROM raw_events e WHERE %s LIMIT 1" % " AND ".join([match_sql, *scope_where])
    return get_conn().execute(sql, [*match_params, *scope_params]).fetchone() is not None


def search_events(
    keywords: list[str],
    *,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    sources: Optional[list[str]] = None,
    buckets: Optional[list[str]] = None,
    event_type: "str | Iterable[str] | None" = "semantic_event",
    limit: int = 20,
    include_penpal_echoes: bool = False,
) -> list[dict]:
    """Keyword search over raw_events: OR across keywords, ranked by how many distinct keywords a
    document matches (coverage), then a per-document score, then the source's semantic_weight,
    then recency. (Recency is a unique key, ts + id, so weight must sit before it to ever act.)
    Rows with identical text (same
    source family and bucket) are then folded into one hit with occurrences / first_seen / last_seen,
    before LIMIT; counts cover the (<= 500 newest) in-scope candidates. Browser pages fold across
    sources (see _collapse) and carry also_seen_in. Superseded snapshots are never candidates.

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
    scope = _scope_clauses(
        since_ts=parse_time_bound(since), until_ts=parse_time_bound(until, end_of_day=True), sources=sources,
        buckets=buckets, event_type=event_type, include_penpal_echoes=include_penpal_echoes)
    if scope is None:
        return []
    match_sql, params = _match_clause(terms)
    scope_where, scope_params = scope
    where = [match_sql, *scope_where]
    params = [*params, *scope_params]
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
    scored = []
    for r in rows:
        found = _matched(r, terms)
        if found:
            scored.append((len(found), round(_doc_score(r, terms), 6), r["semantic_weight"] or 0.0, r, found))
    # Stable sort over a recency-ordered list (ts DESC, id DESC): coverage, document score,
    # semantic_weight, then recency. Deterministic for identical input.
    scored.sort(key=lambda x: x[:3], reverse=True)
    hits = []
    for r, found, occurrences, first_seen, last_seen, also_seen_in in _collapse(scored)[:limit]:
        d = dict(r)
        d.pop("semantic_weight", None)
        d["snippet"] = _snippet(display_text(d), found)
        for k in ("content_text", "window_title", "app_name", "event_type", "ts_end"):
            d.pop(k, None)
        d["matched"] = len(found)
        d["matched_terms"] = list(found)  # which normalised keywords this hit contains (per-keyword feedback)
        d["keyword_count"] = len(terms)
        d["occurrences"] = occurrences
        d["first_seen"] = first_seen
        d["last_seen"] = last_seen
        d["also_seen_in"] = also_seen_in  # other sources that recorded this same item (browser pages)
        hits.append(d)
    return hits


_SOURCE_FAMILY = {"chrome_history": "browser", "safari_history": "browser", "browser_url": "browser"}

# Browser page rows: the page title is what they are about. Sources that carry the page title in
# content_text (history plugins, browser_url) and window rows whose app is a browser (title in
# window_title, with the browser name / "已固定" / unread counter around it) fold by that title.
_PAGE_TITLE_SOURCES = frozenset({"chrome_history", "safari_history", "browser_url"})
_BROWSER_NAMES = tuple(sorted({name.casefold() for name in DEFAULT_SUPPORTED_BROWSERS}))
_BROWSER_SUFFIX = re.compile(r"\s*-\s*(?:" + "|".join(re.escape(n) for n in _BROWSER_NAMES) + r")$")
_PINNED_SUFFIX = re.compile(r"\s*-?\s*已固定$")  # Chinese Chrome pinned-tab marker
_UNREAD_PREFIX = re.compile(r"^\(\d+\)\s*")
_PAGE_KEY = "page"


def _is_browser_app(app_name: Optional[str]) -> bool:
    return bool(app_name) and app_name.strip().casefold() in _BROWSER_NAMES


def _canonical_page_title(text: Optional[str]) -> str:
    """Lowercased, whitespace-collapsed title without browser suffix, pinned marker or unread counter."""
    title = " ".join(str(text or "").casefold().split())
    while True:
        before = title
        title = _UNREAD_PREFIX.sub("", title)
        title = _BROWSER_SUFFIX.sub("", title)
        title = _PINNED_SUFFIX.sub("", title)
        if title == before:
            return title.strip()


def _page_title(r) -> str:
    """Canonical page title of a browser-family row, or '' when the row is not one / has no title."""
    if r["source"] in _PAGE_TITLE_SOURCES:
        return _canonical_page_title(r["content_text"])
    if r["source"] == "window" and _is_browser_app(r["app_name"]):
        return _canonical_page_title(r["window_title"])
    return ""


def _collapse_key(r) -> tuple:
    page = _page_title(r)
    if page:
        return (_PAGE_KEY, r["bucket"], page)
    # The context label of imported rows is not part of the fold key: folding stays what it was.
    title = "" if r["source"] in CONTEXT_LABEL_SOURCES else (r["window_title"] or "")
    text = " ".join(((r["content_text"] or "") + " | " + title + " | " + (r["app_name"] or "")).lower().split())
    return (_SOURCE_FAMILY.get(r["source"], r["source"]), r["bucket"], text)


def _collapse(scored: list) -> list:
    """Group ranked rows into one hit each. Rows with the same normalised text (same source family and
    bucket) fold together. Browser page rows fold across sources too (same canonical page title and
    bucket, at any time, like identical text; get_timeline answers "which time"). The best-ranked row is the representative
    (its own evidence is kept); the group carries occurrences / first_seen / last_seen and also_seen_in
    (the other distinct sources). It only sees rows the WHERE clause already admitted (grant buckets and
    sources), and runs before LIMIT, so it can neither merge nor count anything outside the grant.
    Groups keep the rank order of their best member."""
    groups: dict[tuple, list] = {}
    for index, item in enumerate(scored):
        groups.setdefault(_collapse_key(item[3]), []).append((index, item))
    clusters: list[list] = []
    for key, members in groups.items():
        clusters.append(members)
    clusters.sort(key=lambda c: c[0][0])
    out = []
    for members in clusters:
        items = [m[1] for m in members]
        rep = items[0]
        times = [m[3]["occurred_at"] for m in items]
        also_seen_in = sorted({m[3]["source"] for m in items} - {rep[3]["source"]})
        out.append((rep[3], rep[4], len(items), min(times), max(times), also_seen_in))
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
    include_penpal_echoes: bool = False,
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
    if not include_penpal_echoes:
        where.append(_NOT_ECHO_SQL)
    where.append(_NOT_SUPERSEDED_SQL)
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


def source_coverage(
    *,
    buckets: list[str],
    sources: Optional[list[str]],
    since: Optional[str],
    until: Optional[str],
    event_type: "str | Iterable[str]" = "semantic_event",
    include_penpal_echoes: bool = False,
) -> list[dict]:
    """Per-source data span inside the scope: one grouped query, same WHERE scope rules as search
    (buckets / sources / event types / echo filter). The time range does NOT narrow the span; it only
    sets `has_events_in_range`. Only sources with at least one in-scope row appear, and nothing
    outside the scope can influence the result (no names, no counts). `since` / `until` are already
    normalised UTC ISO bounds (or None)."""
    types = _types(event_type)
    if not buckets or not types or (sources is not None and not sources):
        return []
    where = ["e.event_type IN (%s)" % ",".join("?" * len(types)), "e.bucket IN (%s)" % ",".join("?" * len(buckets))]
    where_params: list = [*types, *buckets]
    if sources is not None:
        where.append("e.source IN (%s)" % ",".join("?" * len(sources)))
        where_params.extend(sources)
    if not include_penpal_echoes:
        where.append(_NOT_ECHO_SQL)
    rows = get_conn().execute(
        f"""
        SELECT e.source AS source, MIN(e.ts_start) AS earliest, MAX(e.ts_start) AS latest,
               MAX((? IS NULL OR e.ts_start >= ?) AND (? IS NULL OR e.ts_start <= ?)) AS in_range
        FROM raw_events e
        WHERE {' AND '.join(where)}
        GROUP BY e.source
        ORDER BY e.source
        """,
        [since, since, until, until, *where_params],
    ).fetchall()
    return [
        {"source": r["source"], "earliest": r["earliest"], "latest": r["latest"], "has_events_in_range": bool(r["in_range"])}
        for r in rows
    ]
