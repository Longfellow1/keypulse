"""Migration v20 (D20): V1 mainline schema on top of the legacy v19 database.

Runs inside the caller's transaction (no implicit commits): DDL in SQLite is
transactional, so a failure anywhere rolls the whole step back and the DB
stays at v19. Every statement is guarded (IF NOT EXISTS / column probes) so a
rerun against a partially-upgraded DB is harmless.

Schema additions:
  raw_events  + source_id, external_id, bucket, kind
  sources, sync_cursors, evidence, access_grants
  raw_events_fts (FTS5 trigram, external-content) + sync triggers
"""
from __future__ import annotations

import sqlite3

_RAW_EVENT_COLUMNS = (
    ("source_id", "INTEGER"),
    ("external_id", "TEXT"),
    ("bucket", "TEXT"),
    ("kind", "TEXT"),
)

_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        locator TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_sync TEXT,
        health TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(type, locator)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_cursors (
        source_id INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
        cursor TEXT,
        last_success TEXT,
        last_error TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL REFERENCES raw_events(id) ON DELETE CASCADE,
        locator TEXT NOT NULL,
        offset_or_line INTEGER,
        content_hash TEXT,
        available INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_evidence_event ON evidence(event_id)",
    # Scope columns (buckets / sources / operations / expiry) arrive with #19
    # via ALTER TABLE ... ADD COLUMN; nothing here needs to change for that.
    """
    CREATE TABLE IF NOT EXISTS access_grants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        revoked_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_access_grants_client ON access_grants(client_id)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_events_source_external
    ON raw_events(source_id, external_id) WHERE external_id IS NOT NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_raw_events_bucket ON raw_events(bucket)",
    # trigram: substring match for any language (CJK has no word breaks).
    # Queries shorter than 3 chars cannot use it; the search layer falls back.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS raw_events_fts USING fts5(
        content_text,
        window_title,
        content='raw_events',
        content_rowid='id',
        tokenize='trigram'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS raw_events_fts_ai AFTER INSERT ON raw_events BEGIN
        INSERT INTO raw_events_fts(rowid, content_text, window_title)
        VALUES (new.id, new.content_text, new.window_title);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS raw_events_fts_ad AFTER DELETE ON raw_events BEGIN
        INSERT INTO raw_events_fts(raw_events_fts, rowid, content_text, window_title)
        VALUES ('delete', old.id, old.content_text, old.window_title);
        DELETE FROM evidence WHERE event_id = old.id;
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS raw_events_fts_au
    AFTER UPDATE OF content_text, window_title ON raw_events BEGIN
        INSERT INTO raw_events_fts(raw_events_fts, rowid, content_text, window_title)
        VALUES ('delete', old.id, old.content_text, old.window_title);
        INSERT INTO raw_events_fts(rowid, content_text, window_title)
        VALUES (new.id, new.content_text, new.window_title);
    END
    """,
)


def rebuild_events_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS index from raw_events (use after bulk edits / corruption)."""
    conn.execute("INSERT INTO raw_events_fts(raw_events_fts) VALUES ('rebuild')")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate_v20(conn: sqlite3.Connection) -> None:
    existing = _columns(conn, "raw_events")
    for name, decl in _RAW_EVENT_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE raw_events ADD COLUMN {name} {decl}")
    for stmt in _STATEMENTS:
        conn.execute(stmt)
    # Backfill what is derivable for legacy rows: kind mirrors event_type.
    # source_id / external_id / bucket stay NULL (no stable external identity
    # or bucket policy existed in v19).
    conn.execute("UPDATE raw_events SET kind = event_type WHERE kind IS NULL")
    rebuild_events_fts(conn)
