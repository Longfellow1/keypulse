"""Migration v29 (M2-3, issue #63): lightweight result feedback.

  mcp_returned_results  what the server actually handed to each client (event id + evidence id + the
                        redacted search keywords), written at response time. `rate_result` only accepts
                        ids found here, and only for 7 days (TTL enforced on read, purged on write).
  mcp_feedback          the verdicts: correct / wrong / missing, with a redacted, length-capped note.
                        `grant_buckets` snapshots the client's buckets at rating time so an export can
                        reproduce the grant even after the token is rotated or revoked.

Runs inside the caller's transaction (run_migrations stamps the version atomically); every statement is
IF NOT EXISTS, so a rerun is a no-op. No data is backfilled: nothing was recorded before this version.
"""
from __future__ import annotations

import sqlite3

_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS mcp_returned_results (
        client_id   TEXT NOT NULL,
        event_id    INTEGER NOT NULL,
        evidence_id INTEGER,
        returned_at TEXT NOT NULL,
        query       TEXT,
        PRIMARY KEY (client_id, event_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mcp_returned_evidence ON mcp_returned_results(client_id, evidence_id)",
    "CREATE INDEX IF NOT EXISTS idx_mcp_returned_at ON mcp_returned_results(returned_at)",
    """
    CREATE TABLE IF NOT EXISTS mcp_feedback (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at    TEXT NOT NULL,
        client_id     TEXT NOT NULL,
        verdict       TEXT NOT NULL CHECK (verdict IN ('correct', 'wrong', 'missing')),
        event_id      INTEGER,
        evidence_id   INTEGER,
        query         TEXT,
        note          TEXT,
        grant_buckets TEXT NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_mcp_feedback_event ON mcp_feedback(client_id, event_id) WHERE event_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_mcp_feedback_missing ON mcp_feedback(client_id, query) WHERE event_id IS NULL",
)


def migrate_v29(conn: sqlite3.Connection) -> None:
    for statement in _STATEMENTS:
        conn.execute(statement)
