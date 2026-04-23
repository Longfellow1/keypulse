"""Tests for migration v13 (user_present column)."""

import sqlite3
import tempfile
from datetime import datetime, timezone

from keypulse.store.migrations import run_migrations
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


def test_migration_v13_column_exists():
    """Fresh DB migrated to v13 has user_present column with default 1."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    run_migrations(conn)
    conn.close()

    # Verify schema
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "PRAGMA table_info(raw_events)"
    )
    columns = {row[1] for row in cursor.fetchall()}
    assert "user_present" in columns
    conn.close()


def test_migration_v13_default_value():
    """New rows inserted via insert_raw_event default to user_present=1."""
    from pathlib import Path
    from keypulse.store.db import init_db, close

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)

    try:
        init_db(db_path)

        event = RawEvent(
            source="test",
            event_type="test_event",
            ts_start=datetime.now(timezone.utc).isoformat(),
        )
        row_id = insert_raw_event(event)

        # Verify inserted row has user_present=1
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT user_present FROM raw_events WHERE id = ?",
            (row_id,)
        ).fetchone()
        assert row["user_present"] == 1
        conn.close()

    finally:
        close()
        import os
        if db_path.exists():
            os.unlink(db_path)


def test_migration_v13_pre_v13_rows_gain_default():
    """Pre-v13 fixture rows migrated to v13 gain user_present=1."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    # Create a minimal schema up to v12 (without user_present)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE _schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO _schema_version(version, applied_at) VALUES (1, ?)",
        (datetime.now(timezone.utc).isoformat(),)
    )

    # Create minimal raw_events table without user_present
    conn.execute("""
        CREATE TABLE raw_events (
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
        )
    """)

    # Insert a pre-v13 row
    conn.execute(
        """INSERT INTO raw_events
           (source, event_type, ts_start, created_at)
           VALUES (?, ?, ?, ?)""",
        ("test", "test_event", "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    # Now run migrations (should apply v2-v13)
    conn = sqlite3.connect(db_path)
    run_migrations(conn)
    conn.close()

    # Verify pre-v13 row now has user_present=1
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT user_present FROM raw_events WHERE source = 'test'"
    ).fetchone()
    assert row[0] == 1
    conn.close()
