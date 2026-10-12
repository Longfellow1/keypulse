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

# Frozen as of v21: the live DEFAULT_SOURCE_BUCKETS no longer lists the browser-history plugins (they are
# site-bucketed, see store.buckets and migration v27), but a DB upgraded from v20 must still get the
# v21 values here so no row is left NULL (an unmapped row is unreadable by any grant).
_V21_SOURCE_BUCKETS = {
    "claude_code": "Work",
    "codex_cli": "Work",
    "git_log": "Work",
    "zsh_history": "Work",
    "markdown_vault": "Personal",
    "chrome_history": "Personal",
    "safari_history": "Personal",
    "wechat": "Personal",
    "knowledgec": "Personal",
    "spotlight": "Personal",
}

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
    for source, bucket in _V21_SOURCE_BUCKETS.items():
        conn.execute(
            "UPDATE raw_events SET bucket = ? WHERE bucket IS NULL AND source = ?",
            (bucket, source),
        )
