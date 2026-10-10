"""MCP stdio server exposing KeyPulse tools. Auth: env KEYPULSE_TOKEN checked on every call."""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from keypulse.mcp import tools
from keypulse.mcp.auth import TOKEN_ENV


def _default_token() -> Optional[str]:
    return os.environ.get(TOKEN_ENV)


def build_server(token_provider: Callable[[], Optional[str]] = _default_token) -> FastMCP:
    """`token_provider` is read at call time so a revoked/rotated token takes effect immediately."""
    server = FastMCP("keypulse")

    @server.tool()
    def search_context(
        query: str,
        time_range: Optional[dict[str, str]] = None,
        sources: Optional[list[str]] = None,
        limit: int = 10,
    ) -> CallToolResult:
        """Search the user's local KeyPulse history (Claude Code sessions etc.).

        Args:
            query: Words to look for (all must appear). Exact words, not questions.
            time_range: Optional {"start": ISO date/datetime, "end": ISO date/datetime}; filters by when the event happened.
            sources: Optional source names to restrict to, e.g. ["claude_code"].
            limit: Max results (1-100, default 10).

        Returns status "ok" with results (id, snippet, occurred_at, source, bucket,
        evidence_id, match_reason) and coverage; "no_results" when nothing matches
        (never guess content); "permission_denied" when the client token is not valid.
        """
        payload = tools.search_context(token_provider(), query, time_range, sources, limit)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structuredContent=payload,
            isError=payload.get("status") == "permission_denied",
        )

    return server


def serve_stdio() -> None:
    build_server().run(transport="stdio")
