from __future__ import annotations

import sqlite3

from keypulse.store.db import init_db


def test_init_db_applies_all_migrations(tmp_path):
    db_path = tmp_path / "keypulse.db"

    init_db(db_path)

    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    assert "raw_events" in tables
    assert "sessions" in tables
    assert "search_docs" in tables
    assert "policies" in tables
    assert "app_state" in tables
    assert "_schema_version" in tables
