"""MCP call audit log (PRD 3.5.2 / 3.8.3). One row per tool call, allowed or denied.

Stores: time, client id (sanitized, or "unknown"), tool, allow/deny, reason code, bucket names of
the grant, and result_count for allowed calls. Never: query text, snippets, tokens or token hashes
(B6) -- the table has no column that could hold them. The caller never sees reason codes.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from keypulse.store.db import get_conn

logger = logging.getLogger("keypulse.mcp.audit")


def record(
    *,
    client: str,
    tool: str,
    allowed: bool,
    reason_code: str,
    scope: Optional[str] = None,
    result_count: Optional[int] = None,
) -> None:
    """Best-effort insert: an audit failure is logged (without content) and never raises into the
    request path, and never turns a denial into an allow."""
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO mcp_audit(ts, client_id, tool, decision, reason_code, scope, result_count)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                client,
                tool,
                "allow" if allowed else "deny",
                reason_code,
                scope,
                result_count if allowed else None,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        logger.warning("audit write failed")


def recent(limit: int = 50) -> list[dict]:
    rows = get_conn().execute(
        "SELECT id, ts, client_id, tool, decision, reason_code, scope, result_count"
        " FROM mcp_audit ORDER BY id DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(r) for r in rows]
