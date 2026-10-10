"""Tool implementations, independent of the MCP transport (unit-testable)."""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from keypulse.mcp import audit
from keypulse.mcp.auth import Grant, check_access
from keypulse.search.engine import parse_time_bound, search_events

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
    results = [
        {
            "id": f"evt_{h['id']}",
            "snippet": h["snippet"],
            "occurred_at": h["occurred_at"],
            "source": h["source"],
            "bucket": h["bucket"],
            "evidence_id": f"evd_{h['evidence_id']}" if h["evidence_id"] is not None else None,
            # Two tiers (PRD 3.7.3): "strong" = several keywords co-occur (or the only keyword hit);
            # "weak" = a possible clue. Derived from the caller's own keywords and this one document.
            "match": "strong" if h["matched"] >= 2 or h["keyword_count"] == 1 else "weak",
            # Fixed format, no caller text echoed.
            "match_reason": f"{_MATCH_REASON}: {h['matched']} of {h['keyword_count']} keywords",
        }
        for h in hits
    ]
    return {"status": "ok", "results": results, "coverage": coverage}
