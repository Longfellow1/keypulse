from __future__ import annotations

import sqlite3

import pytest

from keypulse.capture.manager import CaptureManager, _derive_speaker
from keypulse.config import Config
from keypulse.store.db import close, get_conn, init_db
from keypulse.store.migrations import run_migrations
from keypulse.store.models import RawEvent


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("keyboard_chunk", "user"),
        ("clipboard", "user"),
        ("manual", "user"),
        ("browser", "user"),
        ("window", "system"),
        ("ax_text", "system"),
        ("ocr", "system"),
        ("idle", "system"),
    ],
)
def test_derive_speaker_by_source(source: str, expected: str) -> None:
    event = RawEvent(source=source, event_type="test_event", ts_start="2026-04-18T09:00:00+00:00")

    assert _derive_speaker(event) == expected


def test_process_event_persists_derived_speaker(tmp_path, monkeypatch) -> None:
    close()
    db_path = tmp_path / "keypulse.db"
    init_db(db_path)

    config = Config.model_validate({"app": {"db_path": str(db_path)}})
    manager = CaptureManager(config)
    monkeypatch.setattr(manager._aggregator, "process", lambda _event: None)
    monkeypatch.setattr(manager._policy, "apply", lambda raw_event: raw_event)
    monkeypatch.setattr("keypulse.capture.manager.set_state", lambda *_args, **_kwargs: None)

    manager._process_event(
        RawEvent(
            source="browser",
            event_type="browser_tab",
            ts_start="2026-04-18T09:00:00+00:00",
            app_name="Safari",
            window_title="Example",
            content_text="https://example.com",
        )
    )

    conn = get_conn()
    row = conn.execute(
        "SELECT speaker FROM raw_events ORDER BY id DESC LIMIT 1"
    ).fetchone()

    assert row["speaker"] == "user"

    close()


def test_run_migrations_is_idempotent_and_adds_speaker_index(tmp_path) -> None:
    db_path = tmp_path / "keypulse.db"
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    run_migrations(conn)
    run_migrations(conn)

    index_row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_raw_events_speaker'"
    ).fetchone()

    assert index_row["name"] == "idx_raw_events_speaker"


def test_legacy_raw_event_rows_default_speaker_to_system_after_migration(tmp_path) -> None:
    db_path = tmp_path / "legacy-keypulse.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
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
        );
        INSERT INTO raw_events (
            source, event_type, ts_start, app_name, window_title, process_name,
            content_text, content_hash, metadata_json, sensitivity_level,
            skipped_reason, session_id, created_at
        ) VALUES (
            'window', 'window_focus', '2026-04-18T09:00:00+00:00', 'Notes',
            'Old title', 'com.apple.Notes', 'body', 'hash', NULL, 0, NULL,
            NULL, '2026-04-18T09:00:00+00:00'
        );
        CREATE TABLE _schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL
        );
        INSERT INTO _schema_version(version, applied_at) VALUES (
            10, '2026-04-18T09:00:00+00:00'
        );
        """
    )
    conn.commit()

    run_migrations(conn)

    row = conn.execute("SELECT speaker FROM raw_events LIMIT 1").fetchone()

    assert row["speaker"] == "system"
