"""Tool implementations, independent of the MCP transport (unit-testable)."""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from keypulse.mcp.auth import Grant, authenticate
from keypulse.search.engine import parse_time_bound, search_events

# Raw capture streams (keyboard chunks / OCR / clipboard / AX text / window titles) stay OUT of
# MCP search in P0 even inside a granted bucket: only events ingested through the source plugins
# (event_type "semantic_event") are searchable. Their text is best-effort filtered (D13) and their
# bucket comes from app rules only, so a bucket label is not enough evidence that they are safe to
# hand to a third-party AI. Revisit in P1 together with sensitivity tags.
SEARCHABLE_EVENT_TYPE = "semantic_event"
MAX_QUERY_CHARS = 1000

_MATCH_REASON = "keyword match"


def permission_denied() -> dict[str, Any]:
    """Identical for missing / wrong / revoked tokens; carries no data, counts or hints."""
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
    query: Any,
    time_range: Any = None,
    sources: Any = None,
    limit: Any = 10,
) -> dict[str, Any]:
    grant: Optional[Grant] = authenticate(token)
    if grant is None or not grant.allows("read"):
        return permission_denied()

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
    if not isinstance(query, str) or not query.strip():
        return {"status": "invalid_argument", "message": "query must be a non-empty string"}
    if len(query) > MAX_QUERY_CHARS:
        return {"status": "invalid_argument", "message": f"query must be at most {MAX_QUERY_CHARS} characters"}

    # Scope is enforced inside the SQL WHERE (before LIMIT). A requested source outside the grant
    # simply matches nothing: same answer as a source that does not exist.
    effective_sources = sources
    if grant.sources is not None:
        effective_sources = list(grant.sources) if sources is None else [x for x in sources if x in grant.sources]

    try:
        hits = search_events(
            query,
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
    results = [
        {
            "id": f"evt_{h['id']}",
            "snippet": h["snippet"],
            "occurred_at": h["occurred_at"],
            "source": h["source"],
            "bucket": h["bucket"],
            "evidence_id": f"evd_{h['evidence_id']}" if h["evidence_id"] is not None else None,
            "match_reason": f"{_MATCH_REASON}: {', '.join(h['matched_terms'])}",
        }
        for h in hits
    ]
    return {"status": "ok", "results": results, "coverage": coverage}
