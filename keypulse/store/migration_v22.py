"""Migration v22 (#20): client whitelist, token rotation marker, MCP audit log.

  client_whitelist  clients the user approved (PRD 3.8.3). `mcp grant` adds, `mcp revoke` removes.
                    Backfilled from grants that are active today so upgrading does not lock them
                    out (their Host config must still add KEYPULSE_CLIENT).
  access_grants.rotated_at  set when `mcp rotate` replaces the token (the row, and so the scope, stays).
  mcp_audit         one row per tool call, allowed or denied. By design it has NO column for query
                    text, snippets, tokens or token hashes (B6).
"""
from __future__ import annotations

import sqlite3


def migrate_v22(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS client_whitelist ("
        " client_id TEXT PRIMARY KEY, approved_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO client_whitelist(client_id, approved_at)"
        " SELECT client_id, MIN(created_at) FROM access_grants WHERE revoked_at IS NULL GROUP BY client_id"
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(access_grants)")}
    if "rotated_at" not in existing:
        conn.execute("ALTER TABLE access_grants ADD COLUMN rotated_at TEXT")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mcp_audit ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts TEXT NOT NULL,"
        " client_id TEXT NOT NULL,"
        " tool TEXT NOT NULL,"
        " decision TEXT NOT NULL CHECK (decision IN ('allow','deny')),"
        " reason_code TEXT NOT NULL,"
        " scope TEXT,"
        " result_count INTEGER)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_audit_ts ON mcp_audit(ts)")
