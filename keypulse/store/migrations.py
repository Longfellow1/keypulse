import sqlite3
from datetime import datetime, timezone

SCHEMA_VERSION = 1

MIGRATIONS = [
    # v1
    """
    CREATE TABLE IF NOT EXISTS raw_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        event_type TEXT NOT NULL,
        ts_start TEXT NOT NULL,
        ts_end TEXT,
        app_name TEXT,
        window_title TEXT,
        process_name TEXT,
        content_text TEXT,
        content_hash TEXT,
        metadata_json TEXT,
        sensitivity_level INTEGER DEFAULT 0,
        skipped_reason TEXT,
        session_id TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        ended_at TEXT NOT NULL,
        app_name TEXT,
        primary_window_title TEXT,
        duration_sec INTEGER DEFAULT 0,
        event_count INTEGER DEFAULT 0,
        summary TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS search_docs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ref_type TEXT NOT NULL,
        ref_id TEXT NOT NULL,
        title TEXT,
        body TEXT,
        tags TEXT,
        app_name TEXT,
        created_at TEXT NOT NULL
    );
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS search_docs_fts USING fts5(
        title,
        body,
        app_name,
        content=search_docs,
        content_rowid=id
    );
    """,
    """
    CREATE TRIGGER IF NOT EXISTS search_docs_ai AFTER INSERT ON search_docs BEGIN
        INSERT INTO search_docs_fts(rowid, title, body, app_name)
        VALUES (new.id, new.title, new.body, new.app_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS search_docs_ad AFTER DELETE ON search_docs BEGIN
        INSERT INTO search_docs_fts(search_docs_fts, rowid, title, body, app_name)
        VALUES ('delete', old.id, old.title, old.body, old.app_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS search_docs_au AFTER UPDATE ON search_docs BEGIN
        INSERT INTO search_docs_fts(search_docs_fts, rowid, title, body, app_name)
        VALUES ('delete', old.id, old.title, old.body, old.app_name);
        INSERT INTO search_docs_fts(rowid, title, body, app_name)
        VALUES (new.id, new.title, new.body, new.app_name);
    END;
    """,
    """
    CREATE TABLE IF NOT EXISTS policies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope_type TEXT NOT NULL,
        scope_value TEXT NOT NULL,
        mode TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 100,
        config_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS app_state (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_raw_events_ts_start ON raw_events(ts_start);
    CREATE INDEX IF NOT EXISTS idx_raw_events_source ON raw_events(source);
    CREATE INDEX IF NOT EXISTS idx_raw_events_app_name ON raw_events(app_name);
    CREATE INDEX IF NOT EXISTS idx_raw_events_session_id ON raw_events(session_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions(started_at);
    CREATE INDEX IF NOT EXISTS idx_search_docs_ref ON search_docs(ref_type, ref_id);
    """,
    """
    ALTER TABLE raw_events ADD COLUMN speaker TEXT NOT NULL DEFAULT 'system';
    CREATE INDEX IF NOT EXISTS idx_raw_events_speaker ON raw_events(speaker);
    """,
    """
    ALTER TABLE raw_events ADD COLUMN semantic_weight REAL NOT NULL DEFAULT 0.5;
    CREATE INDEX IF NOT EXISTS idx_raw_events_weight ON raw_events(semantic_weight);
    UPDATE raw_events SET semantic_weight = CASE source
        WHEN 'keyboard_chunk' THEN 1.0
        WHEN 'clipboard' THEN 0.9
        WHEN 'manual' THEN 1.0
        WHEN 'browser' THEN 0.85
        WHEN 'ax_text' THEN 0.8
        WHEN 'ax_ime_commit' THEN 0.9
        WHEN 'ax_snapshot_fallback' THEN 0.5
        WHEN 'ocr_text' THEN 0.4
        WHEN 'window_focus_session' THEN 0.2
        ELSE 0.5
    END;
    """,
    # v13
    """
    ALTER TABLE raw_events ADD COLUMN user_present INTEGER NOT NULL DEFAULT 1;
    CREATE INDEX IF NOT EXISTS idx_raw_events_user_present ON raw_events(user_present);
    """,
    # v14
    """
    CREATE TABLE IF NOT EXISTS profile_identity (
        key TEXT PRIMARY KEY, value TEXT, confidence REAL, source TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS profile_entities (
        alias TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, kind TEXT, weight REAL DEFAULT 1.0
    );
    CREATE TABLE IF NOT EXISTS profile_projects (
        name TEXT PRIMARY KEY, path TEXT, summary TEXT, tech_stack TEXT, status TEXT, last_active TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_profile_entities_canonical ON profile_entities(canonical_name);
    """,
]


def run_migrations(conn: sqlite3.Connection):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    conn.commit()
    row = conn.execute("SELECT MAX(version) FROM _schema_version").fetchone()
    current = row[0] if row[0] is not None else 0
    for i, sql in enumerate(MIGRATIONS, start=1):
        if i > current:
            conn.executescript(sql.strip())
            conn.execute(
                "INSERT INTO _schema_version(version, applied_at) VALUES (?, ?)",
                (i, datetime.now(timezone.utc).isoformat())
            )
            conn.commit()
