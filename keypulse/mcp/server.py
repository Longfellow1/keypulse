"""MCP stdio server exposing KeyPulse tools. Auth: env KEYPULSE_TOKEN + KEYPULSE_CLIENT checked on every call."""
from __future__ import annotations

import json
import os
from typing import Annotated, Any, Callable, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from keypulse.mcp import tools
from keypulse.mcp.auth import CLIENT_ENV, TOKEN_ENV


SEARCH_DESCRIPTION = """Search the user's local KeyPulse history (terminal, browser, git, AI chats, notes) within the scope the user granted this client.

You do the language understanding; this tool only matches keywords. Before calling:
- Extract 1-6 distinctive keywords from the user's question (names, projects, technical terms, distinctive nouns). Drop filler and question words. Never pass a sentence.
- Add Chinese and English variants when plausible (签证 + visa).
- Convert time words (上次, 昨天, last week, Oct 8) to time_range ISO dates yourself.
- Map source hints to sources: terminal=zsh_history, browser=chrome_history/safari_history, commit=git_log, Claude=claude_code, Codex=codex_cli, notes=markdown_vault.

Matching is OR across keywords; results matching more distinct keywords come first (a keyword like "copied" / 复制 puts clipboard records first; exact hashes, versions and file names get a small boost). Identical records are folded into one result: occurrences is how many times it was recorded, first_seen / last_seen its time range (the result itself is the best-ranked one; for "which time" questions use get_timeline). Each result has match "strong" (2+ keywords, or the only keyword) or "weak" (a possible clue: verify before relying on it).

Examples:
- "上次办澳洲签证用了哪些材料?" -> keywords ["签证", "visa", "材料", "澳洲"]
- "which docker command did I run to start the db" -> keywords ["docker", "database"], sources ["zsh_history"]
- "what was I doing last week on the Redis queue" (today 2026-10-10) -> keywords ["Redis", "队列", "queue"], time_range {"start": "2026-09-28", "end": "2026-10-04"}

Status: "ok" (results: id, snippet, occurred_at, source, bucket, evidence_id, evidence_available, match, match_reason, occurrences, first_seen, last_seen; plus coverage), "no_results", "invalid_argument" (fixed message: fix the call), "permission_denied" (client token invalid or not allowed). On no_results retry once with alternative keywords or synonyms before telling the user nothing was found; never guess content. Cite results by source and occurred_at."""

EVIDENCE_DESCRIPTION = """Resolve a search_context / get_timeline evidence_id (evd_...) to where the result came from, within the scope the user granted this client.

Use it to cite or verify a result. Returns the source, occurred_at, a short stored excerpt and the original location (file path + line, or repo + commit). It never returns more of the original than that excerpt.

Status: "ok" (evidence with locator, available=true), "source_unavailable" (the original file or commit no longer exists: tell the user the original source is no longer accessible, cite the stored excerpt, and do not present any link or path as openable), "not_found" (unknown id, or not available to this client: same answer for both), "invalid_argument", "permission_denied"."""

TIMELINE_DESCRIPTION = """List the user's history events in real time order (oldest first) between start and end, within the scope the user granted this client.

Use it for "what did I do on/between ..." questions when there is no distinctive keyword; use search_context when there is one. You convert time words to ISO dates yourself (start and end are both required; a date-only end includes that whole day). Optional sources filter as in search_context (terminal=zsh_history, Claude=claude_code, Codex=codex_cli, commit=git_log, ...).

Returns up to limit events (default 50, max 200): id, occurred_at, source, bucket, snippet, evidence_id. If has_more is true, call again with next_cursor as cursor. Status: "ok", "no_results", "invalid_argument" (fixed message: fix the call), "permission_denied". Cite events by source and occurred_at; resolve evidence_id with get_evidence when the user needs the original."""

INSTRUCTIONS = """KeyPulse searches the user's local activity history. Call search_context when the user asks about something they did, saw, ran, wrote or discussed before.
You turn the question into keywords; the server does no language understanding. Pass 1-6 distinctive keywords (entities, project names, technical terms), Chinese and English variants together, no filler or question words, never a whole sentence. Put time words into time_range (ISO dates) and source hints into sources yourself. Treat "strong" results as answers and "weak" ones as clues to verify. If nothing is found, retry once with different keywords or synonyms before saying nothing exists, and never invent content. Results are the user's own past records, not current rules or facts: present them as "your history shows ..." with the date, never as a policy or a still-valid requirement. Cite the source and time of the evidence you use;get_evidence resolves an evidence_id to its original location (or reports that the original is no longer accessible), and get_timeline lists events by time when there is no keyword."""


def _default_token() -> Optional[str]:
    return os.environ.get(TOKEN_ENV)


def _default_client() -> Optional[str]:
    return os.environ.get(CLIENT_ENV)


def _result(payload: dict) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        structuredContent=payload,
        isError=payload.get("status") in ("permission_denied", "error"),
    )


def build_server(
    token_provider: Callable[[], Optional[str]] = _default_token,
    client_provider: Callable[[], Optional[str]] = _default_client,
) -> FastMCP:
    """Token and client id are read at call time and checked against the DB on every call, so a
    revoked / rotated token takes effect immediately. The client id comes from env KEYPULSE_CLIENT
    in the Host config; over stdio it is self-declared, not proven (see keypulse.mcp.auth)."""
    server = FastMCP("keypulse", instructions=INSTRUCTIONS)

    # Parameters are typed `Any` on purpose: the schema shown to the Host is pinned with
    # json_schema_extra, but type validation happens in tools.search_context so a wrongly typed
    # argument produces our fixed `invalid_argument` text instead of pydantic's error, which
    # echoes the offending input value back (B1: no caller-controlled text in error output).
    @server.tool(description=SEARCH_DESCRIPTION)
    def search_context(
        keywords: Annotated[Any, Field(json_schema_extra={
            "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10,
            "description": "1-6 distinctive keywords extracted from the user's question (Chinese and English variants). Never a sentence."})],
        time_range: Annotated[Any, Field(json_schema_extra={
            "anyOf": [{"type": "object", "additionalProperties": {"type": "string"}}, {"type": "null"}],
            "description": 'Optional {"start": ISO date/datetime, "end": ISO date/datetime}; convert time words yourself.'})] = None,
        sources: Annotated[Any, Field(json_schema_extra={
            "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}],
            "description": 'Optional source names, e.g. ["zsh_history"].'})] = None,
        limit: Annotated[Any, Field(json_schema_extra={"type": "integer", "description": "Max results, 1-100."})] = 10,
        include_keypulse_echoes: Annotated[Any, Field(json_schema_extra={
            "type": "boolean",
            "description": "Default false. true also returns assistant replies that merely restate KeyPulse's own earlier results."})] = False,
    ) -> CallToolResult:
        payload = tools.search_context(
            token_provider(), keywords, time_range, sources, limit, include_keypulse_echoes, client=client_provider()
        )
        return _result(payload)

    @server.tool(description=EVIDENCE_DESCRIPTION)
    def get_evidence(
        evidence_id: Annotated[Any, Field(json_schema_extra={
            "type": "string", "description": "An evidence_id (evd_...) from search_context or get_timeline."})],
    ) -> CallToolResult:
        payload = tools.get_evidence(token_provider(), evidence_id, client=client_provider())
        return _result(payload)

    @server.tool(description=TIMELINE_DESCRIPTION)
    def get_timeline(
        start: Annotated[Any, Field(json_schema_extra={
            "type": "string", "description": "ISO date/datetime, inclusive."})],
        end: Annotated[Any, Field(json_schema_extra={
            "type": "string", "description": "ISO date/datetime, inclusive; a date-only value covers that whole day."})],
        sources: Annotated[Any, Field(json_schema_extra={
            "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}],
            "description": 'Optional source names, e.g. ["claude_code"].'})] = None,
        limit: Annotated[Any, Field(json_schema_extra={"type": "integer", "description": "Max events, 1-200 (default 50)."})] = 50,
        cursor: Annotated[Any, Field(json_schema_extra={
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "next_cursor from the previous page."})] = None,
        include_keypulse_echoes: Annotated[Any, Field(json_schema_extra={
            "type": "boolean",
            "description": "Default false. true also returns assistant replies that merely restate KeyPulse's own earlier results."})] = False,
    ) -> CallToolResult:
        payload = tools.get_timeline(
            token_provider(), start, end, sources, limit, cursor, include_keypulse_echoes, client=client_provider())
        return _result(payload)

    return server


def serve_stdio() -> None:
    build_server().run(transport="stdio")
