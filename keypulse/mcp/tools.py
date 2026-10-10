"""Tool implementations, independent of the MCP transport (unit-testable)."""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from keypulse.mcp import audit
from keypulse.mcp.auth import Grant, check_access
from keypulse.search.engine import (
    TIMELINE_DEFAULT_LIMIT, TIMELINE_MAX_LIMIT, parse_time_bound, scoped_evidence, search_events, timeline_events,
)
from keypulse.store.evidence import refresh_availability, split_git_locator

# Raw capture streams (keyboard chunks / OCR / clipboard / AX text / window titles) stay OUT of
# MCP search in P0 even inside a granted bucket: only events ingested through the source plugins
# (event_type "semantic_event") are searchable. Their text is best-effort filtered (D13) and their
# bucket comes from app rules only, so a bucket label is not enough evidence that they are safe to
# hand to a third-party AI. Revisit in P1 together with sensitivity tags.
SEARCHABLE_EVENT_TYPE = "semantic_event"
MAX_KEYWORDS = 10
MAX_KEYWORD_CHARS = 100
# A keyword that looks like a whole sentence means the host skipped the keyword extraction step.
MAX_KEYWORD_CJK_CHARS = 12
MAX_KEYWORD_WORDS = 6
TOOL_SEARCH_CONTEXT = "search_context"
TOOL_GET_EVIDENCE = "get_evidence"
TOOL_GET_TIMELINE = "get_timeline"
EXCERPT_CHARS = 400
EVIDENCE_NOT_FOUND = {"status": "not_found", "message": "No evidence with this id is available to this client."}
SOURCE_UNAVAILABLE_MESSAGE = "原始出处已不可访问 / original source is no longer accessible"

_MATCH_REASON = "keyword match"
SENTENCE_KEYWORD_MESSAGE = (
    "Each keyword must be a short word or phrase, not a sentence or question. "
    "Split the question into 1-6 distinctive keywords and call again."
)
KEYWORDS_MESSAGE = "keywords must be a non-empty list of 1-10 strings"


def _looks_like_sentence(keyword: str) -> bool:
    cjk = sum(1 for ch in keyword if "\u4e00" <= ch <= "\u9fff")
    return (
        keyword.endswith(("?", "\uff1f"))
        or cjk > MAX_KEYWORD_CJK_CHARS
        or len(keyword.split()) > MAX_KEYWORD_WORDS
        or len(keyword) > MAX_KEYWORD_CHARS
    )


def permission_denied() -> dict[str, Any]:
    """One body for EVERY deny reason (missing / wrong / revoked / rotated / expired token, bad or
    non-whitelisted client, no read right): no data, counts or hints. Reasons live in the audit log only."""
    return {
        "status": "permission_denied",
        "reason_code": "TOKEN_INVALID",
        "message": "KeyPulse access was not granted for this client.",
        "next_action": "review_access_in_keypulse",
    }


def _parse_time_range(time_range: Any) -> tuple[Optional[str], Optional[str]]:
    if time_range is None:
        return None, None
    if not isinstance(time_range, dict):
        raise ValueError("time_range must be an object with optional 'start' and 'end'")
    unknown = set(time_range) - {"start", "end"}
    if unknown:
        raise ValueError("time_range accepts only 'start' and 'end'")
    return parse_time_bound(time_range.get("start")), parse_time_bound(time_range.get("end"), end_of_day=True)


def _coverage(grant: Grant, start: Optional[str], end: Optional[str]) -> dict[str, Any]:
    """What this call searched: the caller's own grant scope only. Must never carry names, counts
    or existence of anything outside it (B1 / G1); no query or requested-source echo either."""
    return {
        "partial": False,
        "note": "Searched indexed source-plugin history within this client's granted scope.",
        "time_range": {"start": start, "end": end},
        "buckets": list(grant.buckets),
        "sources": list(grant.sources) if grant.sources is not None else None,
    }


def search_context(
    token: Optional[str],
    keywords: Any,
    time_range: Any = None,
    sources: Any = None,
    limit: Any = 10,
    *,
    client: Optional[str] = None,
) -> dict[str, Any]:
    decision = check_access(token, client, "read")
    if decision.grant is None:
        audit.record(client=decision.client, tool=TOOL_SEARCH_CONTEXT, allowed=False,
                     reason_code=decision.reason, scope=decision.scope)
        return permission_denied()
    payload = _search(decision.grant, keywords, time_range, sources, limit)
    count = len(payload.get("results") or [])
    audit.record(client=decision.client, tool=TOOL_SEARCH_CONTEXT, allowed=True,
                 reason_code=payload.get("status", "error"), scope=decision.scope, result_count=count)
    return payload


def _search(grant: Grant, keywords: Any, time_range: Any, sources: Any, limit: Any) -> dict[str, Any]:

    # Fixed-text validation: caller-supplied values are never echoed back.
    if sources is not None and not (isinstance(sources, list) and all(isinstance(x, str) for x in sources)):
        return {"status": "invalid_argument", "message": "sources must be a list of source names"}
    if isinstance(limit, bool) or not isinstance(limit, int):
        return {"status": "invalid_argument", "message": "limit must be an integer between 1 and 100"}

    try:
        start, end = _parse_time_range(time_range)
    except (ValueError, TypeError):
        # Fixed text: never echo caller-supplied strings (or parser internals) back.
        return {"status": "invalid_argument", "message": "time_range must be an object with optional ISO 'start' and 'end'"}
    if (
        not isinstance(keywords, list)
        or not 1 <= len(keywords) <= MAX_KEYWORDS
        or not all(isinstance(k, str) and k.strip() for k in keywords)
    ):
        return {"status": "invalid_argument", "message": KEYWORDS_MESSAGE}
    if any(_looks_like_sentence(k.strip()) for k in keywords):
        return {"status": "invalid_argument", "message": SENTENCE_KEYWORD_MESSAGE}

    # Scope is enforced inside the SQL WHERE (before LIMIT). A requested source outside the grant
    # simply matches nothing: same answer as a source that does not exist.
    effective_sources = sources
    if grant.sources is not None:
        effective_sources = list(grant.sources) if sources is None else [x for x in sources if x in grant.sources]

    try:
        hits = search_events(
            keywords,
            since=start,
            until=end,
            sources=effective_sources,
            buckets=list(grant.buckets),
            event_type=SEARCHABLE_EVENT_TYPE,
            limit=limit,
        )
    except sqlite3.Error:
        # Never surface SQLite / FTS error text: it can echo query fragments or index internals.
        return {"status": "error", "message": "search failed"}
    coverage = _coverage(grant, start, end)
    if not hits:
        return {"status": "no_results", "results": [], "coverage": coverage}
    availability = refresh_availability(
        [(h["evidence_id"], h["evidence_locator"], h.get("evidence_line"), h.get("evidence_stored_available", 1))
         for h in hits if h["evidence_id"] is not None and h.get("evidence_locator")],
        deep=False,
    )
    results = [
        {
            "id": f"evt_{h['id']}",
            "snippet": h["snippet"],
            "occurred_at": h["occurred_at"],
            "source": h["source"],
            "bucket": h["bucket"],
            "evidence_id": f"evd_{h['evidence_id']}" if h["evidence_id"] is not None else None,
            # Cheap path-existence check only; the line / commit is verified by get_evidence.
            "evidence_available": bool(availability.get(h["evidence_id"], False)),
            # Two tiers (PRD 3.7.3): "strong" = several keywords co-occur (or the only keyword hit);
            # "weak" = a possible clue. Derived from the caller's own keywords and this one document.
            "match": "strong" if h["matched"] >= 2 or h["keyword_count"] == 1 else "weak",
            # Fixed format, no caller text echoed.
            "match_reason": f"{_MATCH_REASON}: {h['matched']} of {h['keyword_count']} keywords",
        }
        for h in hits
    ]
    return {"status": "ok", "results": results, "coverage": coverage}


def _gate(token: Optional[str], client: Optional[str], tool: str):
    decision = check_access(token, client, "read")
    if decision.grant is None:
        audit.record(client=decision.client, tool=tool, allowed=False,
                     reason_code=decision.reason, scope=decision.scope)
        return decision, permission_denied()
    return decision, None


def _parse_evidence_id(raw: str) -> Optional[int]:
    body = raw[4:]
    if raw.startswith("evd_") and body.isascii() and body.isdigit() and len(body) < 19:
        return int(body)
    return None


def _scope_sources(grant: Grant, sources: Optional[list[str]]) -> Optional[list[str]]:
    if grant.sources is None:
        return sources
    return list(grant.sources) if sources is None else [x for x in sources if x in grant.sources]


def get_evidence(token: Optional[str], evidence_id: Any, *, client: Optional[str] = None) -> dict[str, Any]:
    decision, denied = _gate(token, client, TOOL_GET_EVIDENCE)
    if denied is not None:
        return denied
    payload = _get_evidence(decision.grant, evidence_id)
    audit.record(client=decision.client, tool=TOOL_GET_EVIDENCE, allowed=True,
                 reason_code=payload.get("status", "error"), scope=decision.scope,
                 result_count=1 if "evidence" in payload else 0)
    return payload


def _get_evidence(grant: Grant, evidence_id: Any) -> dict[str, Any]:
    if not isinstance(evidence_id, str):
        return {"status": "invalid_argument", "message": "evidence_id must be a string such as evd_123"}
    # Malformed, nonexistent and out-of-scope ids all get the identical not_found body.
    numeric = _parse_evidence_id(evidence_id)
    if numeric is None:
        return dict(EVIDENCE_NOT_FOUND)
    try:
        row = scoped_evidence(numeric, sources=_scope_sources(grant, None), buckets=list(grant.buckets),
                              event_type=SEARCHABLE_EVENT_TYPE)
    except sqlite3.Error:
        return {"status": "error", "message": "evidence lookup failed"}
    if row is None:
        return dict(EVIDENCE_NOT_FOUND)
    available = refresh_availability(
        [(row["evidence_id"], row["locator"], row["line"], row["stored_available"])], deep=True
    )[row["evidence_id"]]
    evidence: dict[str, Any] = {
        "id": f"evd_{row['evidence_id']}",
        "event_id": f"evt_{row['event_id']}",
        "source": row["source"],
        "bucket": row["bucket"],
        "occurred_at": row["occurred_at"],
        # Stored, already-redacted text only. The original file is never read back out.
        "excerpt": " ".join((row["content_text"] or "").split())[:EXCERPT_CHARS],
        "available": available,
        "locator": None,
    }
    if not available:
        # AT21: keep the stored excerpt, give no path / commit that could look openable.
        return {"status": "source_unavailable", "message": SOURCE_UNAVAILABLE_MESSAGE, "evidence": evidence}
    parsed = split_git_locator(row["locator"])
    if parsed is not None:
        evidence["locator"] = {"type": "git", "repo": parsed[0], "commit": parsed[1]}
    else:
        evidence["locator"] = {"type": "file", "path": row["locator"], "line": row["line"]}
    return {"status": "ok", "evidence": evidence}


def _parse_cursor(raw: Any) -> tuple[str, int]:
    if not isinstance(raw, str) or len(raw) > 80:
        raise ValueError("cursor")
    ts, _, ident = raw.rpartition("|")
    if not ts or not ident.isascii() or not ident.isdigit():
        raise ValueError("cursor")
    parsed = parse_time_bound(ts)
    if parsed is None:
        raise ValueError("cursor")
    return ts, int(ident)  # raw: it was issued by us from a stored occurred_at


def get_timeline(
    token: Optional[str],
    start: Any,
    end: Any,
    sources: Any = None,
    limit: Any = TIMELINE_DEFAULT_LIMIT,
    cursor: Any = None,
    *,
    client: Optional[str] = None,
) -> dict[str, Any]:
    decision, denied = _gate(token, client, TOOL_GET_TIMELINE)
    if denied is not None:
        return denied
    payload = _get_timeline(decision.grant, start, end, sources, limit, cursor)
    audit.record(client=decision.client, tool=TOOL_GET_TIMELINE, allowed=True,
                 reason_code=payload.get("status", "error"), scope=decision.scope,
                 result_count=len(payload.get("results") or []))
    return payload


def _get_timeline(grant: Grant, start: Any, end: Any, sources: Any, limit: Any, cursor: Any) -> dict[str, Any]:
    if sources is not None and not (isinstance(sources, list) and all(isinstance(x, str) for x in sources)):
        return {"status": "invalid_argument", "message": "sources must be a list of source names"}
    if isinstance(limit, bool) or not isinstance(limit, int):
        return {"status": "invalid_argument", "message": f"limit must be an integer between 1 and {TIMELINE_MAX_LIMIT}"}
    bad_range = {"status": "invalid_argument",
                 "message": "start and end are required ISO dates or datetimes with start <= end; cursor must come from a previous call"}
    if not isinstance(start, str) or not isinstance(end, str):
        return bad_range
    try:
        since, until = _parse_time_range({"start": start, "end": end})
        after = _parse_cursor(cursor) if cursor is not None else None
    except (ValueError, TypeError):
        return bad_range
    if since is None or until is None or since > until:
        return bad_range
    try:
        rows, has_more = timeline_events(
            since=since, until=until, sources=_scope_sources(grant, sources), buckets=list(grant.buckets),
            after=after, event_type=SEARCHABLE_EVENT_TYPE, limit=limit,
        )
    except sqlite3.Error:
        return {"status": "error", "message": "timeline failed"}
    coverage = _coverage(grant, since, until)
    if not rows:
        return {"status": "no_results", "results": [], "has_more": False, "coverage": coverage}
    results = [
        {
            "id": f"evt_{r['id']}",
            "occurred_at": r["occurred_at"],
            "source": r["source"],
            "bucket": r["bucket"],
            "snippet": r["snippet"],
            "evidence_id": f"evd_{r['evidence_id']}" if r["evidence_id"] is not None else None,
        }
        for r in rows
    ]
    payload: dict[str, Any] = {"status": "ok", "results": results, "has_more": has_more, "coverage": coverage}
    if has_more:
        payload["next_cursor"] = f"{rows[-1]['occurred_at']}|{rows[-1]['id']}"
    return payload
