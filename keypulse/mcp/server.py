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
    server = FastMCP("keypulse")

    # Parameters are typed `Any` on purpose: the schema shown to the Host is pinned with
    # json_schema_extra, but type validation happens in tools.search_context so a wrongly typed
    # argument produces our fixed `invalid_argument` text instead of pydantic's error, which
    # echoes the offending input value back (B1: no caller-controlled text in error output).
    @server.tool()
    def search_context(
        query: Annotated[Any, Field(json_schema_extra={"type": "string"})],
        time_range: Annotated[Any, Field(json_schema_extra={
            "anyOf": [{"type": "object", "additionalProperties": {"type": "string"}}, {"type": "null"}]})] = None,
        sources: Annotated[Any, Field(json_schema_extra={
            "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]})] = None,
        limit: Annotated[Any, Field(json_schema_extra={"type": "integer"})] = 10,
    ) -> CallToolResult:
        """Search the user's local KeyPulse history within the scope the user granted this client.

        Args:
            query: Words to look for (all must appear). Exact words, not questions.
            time_range: Optional {"start": ISO date/datetime, "end": ISO date/datetime}; filters by when the event happened.
            sources: Optional source names to restrict to, e.g. ["claude_code"].
            limit: Max results (1-100, default 10).

        Returns status "ok" with results (id, snippet, occurred_at, source, bucket,
        evidence_id, match_reason) and coverage; "no_results" when nothing matches
        (never guess content); "permission_denied" when the client token is not valid,
        expired, or not allowed to read. Results stay inside the buckets/sources granted to this client.
        """
        payload = tools.search_context(
            token_provider(), query, time_range, sources, limit, client=client_provider()
        )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structuredContent=payload,
            isError=payload.get("status") in ("permission_denied", "error"),
        )

    return server


def serve_stdio() -> None:
    build_server().run(transport="stdio")
