from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from keypulse.sources import registry
from keypulse.sources.plugins.raw_events import RawEventsSource
from keypulse.sources.registry import get_source
from keypulse.sources.types import DataSource


def _make_raw_events_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE raw_events (
                id INTEGER PRIMARY KEY,
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
                speaker TEXT NOT NULL DEFAULT 'system',
                semantic_weight REAL NOT NULL DEFAULT 0.5,
                user_present INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_raw_event(
    db_path: Path,
    *,
    event_id: int,
    source: str,
    event_type: str,
    ts_start: str,
    app_name: str,
    window_title: str,
    content_text: str,
    metadata_json: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO raw_events(
                id, source, event_type, ts_start, ts_end, app_name, window_title,
                process_name, content_text, content_hash, metadata_json, sensitivity_level,
                skipped_reason, session_id, speaker, semantic_weight, user_present, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                source,
                event_type,
                ts_start,
                None,
                app_name,
                window_title,
                "Code",
                content_text,
                None,
                metadata_json,
                0,
                None,
                "sess-1",
                "user",
                0.9,
                1,
                ts_start,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_raw_events_source_is_registered() -> None:
    assert get_source("raw_events") is not None


def test_discover_returns_empty_when_raw_events_table_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "no_raw_events.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE placeholder (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    source = RawEventsSource(db_path=db_path)

    assert source.discover() == []


def test_read_maps_raw_event_and_preserves_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "raw_events.db"
    _make_raw_events_db(db_path)
    ts = "2026-04-28T10:00:00+00:00"
    _insert_raw_event(
        db_path,
        event_id=42,
        source="window",
        event_type="window_focus_session",
        ts_start=ts,
        app_name="Cursor",
        window_title="README.md - keypulse",
        content_text="实现 raw_events source plugin",
        metadata_json=json.dumps({"tags": ["work", "focus"], "topic": "cleaning"}),
    )

    source = RawEventsSource(db_path=db_path)
    instances = source.discover()
    assert len(instances) == 1

    events = list(
        source.read(
            instances[0],
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event.source == "raw_events"
    assert event.actor == "Cursor"
    assert event.time == datetime.fromisoformat(ts)
    assert event.intent == "实现 raw_events source plugin"
    assert event.raw_ref == "raw_events:42"
    assert "README.md - keypulse" in event.artifact
    assert "work" in event.artifact

    assert event.metadata["source_type"] == "window_focus_session"
    assert event.metadata["topic"] == "cleaning"
    assert event.metadata["tags"] == ["work", "focus"]
    assert event.metadata["window_title"] == "README.md - keypulse"
    assert event.metadata["body"] == "实现 raw_events source plugin"
    assert event.metadata["raw_event"]["source"] == "window"
    assert event.metadata["raw_event"]["event_type"] == "window_focus_session"
    assert event.metadata["raw_event"]["metadata_json"]


def test_registry_read_all_filters_low_signal_raw_events(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "cleaning.db"
    _make_raw_events_db(db_path)

    _insert_raw_event(
        db_path,
        event_id=1,
        source="window",
        event_type="window_title_changed",
        ts_start="2026-04-28T09:00:00+00:00",
        app_name="Cursor",
        window_title="",
        content_text="...",
        metadata_json="{}",
    )
    _insert_raw_event(
        db_path,
        event_id=2,
        source="manual",
        event_type="manual_save",
        ts_start="2026-04-28T09:01:00+00:00",
        app_name="Obsidian",
        window_title="Daily Note",
        content_text="整理 cleaning 迁移方案",
        metadata_json="{}",
    )

    raw_source: DataSource = RawEventsSource(db_path=db_path)
    monkeypatch.setattr(registry, "_PLUGINS", {"raw_events": raw_source})

    events = list(
        registry.read_all(
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    assert events[0].raw_ref == "raw_events:2"
    assert events[0].intent == "整理 cleaning 迁移方案"
