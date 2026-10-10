"""Migration v21 (#19): AccessGrant scope + bucket backfill.

access_grants gains the PRD §3.5.2 scope fields:
  buckets     JSON array of bucket names the grant may read (NOT NULL; legacy rows -> ["Work"], least privilege)
  sources     JSON array of source names, NULL = every source inside the granted buckets
  operations  JSON array, subset of ["read","write"]; default ["read"]
  purpose     free text shown to the user
  expires_at  ISO UTC; NULL = no expiry. An expired grant authenticates as nobody.

Backfill: raw_events.bucket is NULL for legacy rows and for sources that had no default when
the row was ingested (v20 only stamped claude_code / codex_cli / git_log). It is filled from the
built-in per-source defaults only (config overrides and app rules are applied at ingest and via
`keypulse rules rebucket`; a migration must not depend on user config). Done here, not as a
maintenance step, because exposure must be decided before the first scoped grant is served.
Only NULL buckets are touched, so a rerun is a no-op.
"""
from __future__ import annotations

import sqlite3

from keypulse.store.buckets import DEFAULT_SOURCE_BUCKETS

_GRANT_COLUMNS = (
    ("buckets", "TEXT NOT NULL DEFAULT '[\"Work\"]'"),
    ("sources", "TEXT"),
    ("operations", "TEXT NOT NULL DEFAULT '[\"read\"]'"),
    ("purpose", "TEXT"),
    ("expires_at", "TEXT"),
)


def migrate_v21(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(access_grants)")}
    for name, decl in _GRANT_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE access_grants ADD COLUMN {name} {decl}")
    for source, bucket in DEFAULT_SOURCE_BUCKETS.items():
        conn.execute(
            "UPDATE raw_events SET bucket = ? WHERE bucket IS NULL AND source = ?",
            (bucket, source),
        )
