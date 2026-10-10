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

Matching is OR across keywords; results matching more distinct keywords come first. Each result has match "strong" (2+ keywords, or the only keyword) or "weak" (a possible clue: verify before relying on it).

Examples:
- "上次办澳洲签证用了哪些材料?" -> keywords ["签证", "visa", "材料", "澳洲"]
- "which docker command did I run to start the db" -> keywords ["docker", "database"], sources ["zsh_history"]
- "what was I doing last week on the Redis queue" (today 2026-10-10) -> keywords ["Redis", "队列", "queue"], time_range {"start": "2026-09-28", "end": "2026-10-04"}

Status: "ok" (results: id, snippet, occurred_at, source, bucket, evidence_id, match, match_reason; plus coverage), "no_results", "invalid_argument" (fixed message: fix the call), "permission_denied" (client token invalid or not allowed). On no_results retry once with alternative keywords or synonyms before telling the user nothing was found; never guess content. Cite results by source and occurred_at."""

INSTRUCTIONS = """KeyPulse searches the user's local activity history. Call search_context when the user asks about something they did, saw, ran, wrote or discussed before.
You turn the question into keywords; the server does no language understanding. Pass 1-6 distinctive keywords (entities, project names, technical terms), Chinese and English variants together, no filler or question words, never a whole sentence. Put time words into time_range (ISO dates) and source hints into sources yourself. Treat "strong" results as answers and "weak" ones as clues to verify. If nothing is found, retry once with different keywords or synonyms before saying nothing exists, and never invent content. Cite the source and time of the evidence you use."""


def _default_token() -> Optional[str]:
    return os.environ.get(TOKEN_ENV)


def _default_client() -> Optional[str]:
    return os.environ.get(CLIENT_ENV)


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
    ) -> CallToolResult:
        payload = tools.search_context(
            token_provider(), keywords, time_range, sources, limit, client=client_provider()
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structuredContent=payload,
            isError=payload.get("status") in ("permission_denied", "error"),
        )

    return server


def serve_stdio() -> None:
    build_server().run(transport="stdio")
