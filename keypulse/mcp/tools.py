"""Tool implementations, independent of the MCP transport (unit-testable)."""
from __future__ import annotations

from typing import Any, Optional

from keypulse.mcp.auth import Grant, authenticate
from keypulse.search.engine import parse_time_bound, search_events

# Until #19 adds bucket/source scopes to AccessGrant, every valid grant reads
# the same set: events ingested through the source plugins (event_type
# "semantic_event"). Raw capture streams (keyboard / OCR / clipboard / AX text)
# are NOT exposed over MCP.
SEARCHABLE_EVENT_TYPE = "semantic_event"

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


def search_context(
    token: Optional[str],
    query: str,
    time_range: Optional[dict] = None,
    sources: Optional[list[str]] = None,
    limit: int = 10,
) -> dict[str, Any]:
    grant: Optional[Grant] = authenticate(token)
    if grant is None:
        return permission_denied()

    try:
        start, end = _parse_time_range(time_range)
    except ValueError as exc:
        return {"status": "invalid_argument", "message": str(exc)}
    if not isinstance(query, str) or not query.strip():
        return {"status": "invalid_argument", "message": "query must be a non-empty string"}

    hits = search_events(
        query,
        since=start,
        until=end,
        sources=sources,
        event_type=SEARCHABLE_EVENT_TYPE,
        limit=limit,
    )
    coverage = {
        "partial": False,
        "note": "Searched indexed Claude Code / source-plugin history within this client's granted scope.",
        "time_range": {"start": start, "end": end},
        "sources": sources,
    }
    if not hits:
        return {"status": "no_results", "query": query, "results": [], "coverage": coverage}
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
    return {"status": "ok", "query": query, "results": results, "coverage": coverage}
