from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from keypulse.sources.approval import ApprovalStore
from keypulse.sources.discoverers import CandidateSource
from keypulse.sources.plugins.approved_sqlite import ApprovedSqliteSource, parse_time_value
from keypulse.sources.registry import get_source


_WEBKIT_EPOCH_OFFSET_US = 11_644_473_600 * 1_000_000
_CF_EPOCH_OFFSET_S = 978_307_200


def _make_db(path: Path, statements: list[tuple[str, tuple[object, ...] | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        for sql, params in statements:
            conn.execute(sql, params or ())
        conn.commit()
    finally:
        conn.close()


def _approve_sqlite(store: ApprovalStore, path: Path, *, app_hint: str = "Cursor") -> str:
    candidate = CandidateSource(
        discoverer="sqlite",
        path=str(path.resolve()),
        app_hint=app_hint,
        schema_signature="messages",
        hint_tables=["messages"],
        confidence="high",
    )
    record = store.approve(candidate, note="test")
    return record.candidate_id


def test_approved_sqlite_source_is_registered() -> None:
    assert get_source("approved_sqlite") is not None


def test_discover_reads_approved_sqlite_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "approved.db"
    _make_db(
        db_path,
        [
            ("CREATE TABLE messages (id INTEGER PRIMARY KEY, timestamp INTEGER, text TEXT)", None),
        ],
    )

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    candidate_id = _approve_sqlite(store, db_path, app_hint="Cursor")

    source = ApprovedSqliteSource(approval_store=store)
    instances = source.discover()

    assert len(instances) == 1
    instance = instances[0]
    assert instance.plugin == "approved_sqlite"
    assert instance.locator == str(db_path.resolve())
    assert instance.metadata["candidate_id"] == candidate_id
    assert instance.metadata["approved_candidate_id"] == candidate_id
    assert instance.metadata["app_hint"] == "Cursor"
    assert "messages" in instance.metadata["hint_tables"]


def test_read_maps_rows_to_semantic_events(tmp_path: Path) -> None:
    in_window = int(datetime(2026, 4, 28, 1, 30, tzinfo=timezone.utc).timestamp())
    out_window = int(datetime(2026, 4, 20, 1, 30, tzinfo=timezone.utc).timestamp())
    db_path = tmp_path / "events.db"
    _make_db(
        db_path,
        [
            ("CREATE TABLE messages (id INTEGER PRIMARY KEY, timestamp INTEGER, text TEXT)", None),
            ("INSERT INTO messages(id, timestamp, text) VALUES (1, ?, ?)", (in_window, "query from approved sqlite")),
            ("INSERT INTO messages(id, timestamp, text) VALUES (2, ?, ?)", (out_window, "too old")),
        ],
    )

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    _approve_sqlite(store, db_path, app_hint="Cursor")
    source = ApprovedSqliteSource(approval_store=store)
    instance = source.discover()[0]

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event.source == "approved_sqlite"
    assert event.actor == "user"
    assert event.intent == "query from approved sqlite"
    assert event.artifact == "Cursor:messages:1"
    assert event.raw_ref.startswith("approved_sqlite:")
    assert event.privacy_tier == "yellow"
    assert event.metadata["table"] == "messages"


def test_read_uses_tempfile_copy2(monkeypatch, tmp_path: Path) -> None:
    in_window = int(datetime(2026, 4, 28, 1, 30, tzinfo=timezone.utc).timestamp())
    db_path = tmp_path / "copy2.db"
    _make_db(
        db_path,
        [
            ("CREATE TABLE messages (id INTEGER PRIMARY KEY, timestamp INTEGER, text TEXT)", None),
            ("INSERT INTO messages(id, timestamp, text) VALUES (1, ?, ?)", (in_window, "copy2 check")),
        ],
    )

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    _approve_sqlite(store, db_path, app_hint="Cursor")
    source = ApprovedSqliteSource(approval_store=store)
    instance = source.discover()[0]

    original_copy2 = __import__("shutil").copy2
    copied: list[tuple[str, str]] = []

    def tracking_copy2(src, dst, *args, **kwargs):
        copied.append((str(src), str(dst)))
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr("keypulse.sources.plugins.approved_sqlite.shutil.copy2", tracking_copy2)

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert copied
    assert len(events) == 1


def test_read_handles_schema_without_time_and_text(tmp_path: Path) -> None:
    db_path = tmp_path / "no-time-no-text.db"
    _make_db(
        db_path,
        [
            ("CREATE TABLE items (id INTEGER PRIMARY KEY, value INTEGER)", None),
            ("INSERT INTO items(id, value) VALUES (1, 42)", None),
        ],
    )

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    _approve_sqlite(store, db_path, app_hint="OddDB")
    source = ApprovedSqliteSource(approval_store=store)
    instance = source.discover()[0]

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert events == []


def test_read_uses_fallback_intent_when_text_column_missing(tmp_path: Path) -> None:
    in_window = int(datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc).timestamp())
    db_path = tmp_path / "missing-text.db"
    _make_db(
        db_path,
        [
            ("CREATE TABLE events (id INTEGER PRIMARY KEY, timestamp INTEGER, value TEXT)", None),
            ("INSERT INTO events(id, timestamp, value) VALUES (9, ?, ?)", (in_window, "payload")),
        ],
    )

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    _approve_sqlite(store, db_path, app_hint="NoText")
    source = ApprovedSqliteSource(approval_store=store)
    instance = source.discover()[0]

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    assert events[0].intent == "events row 1"


def test_read_parses_webkit_microseconds(tmp_path: Path) -> None:
    target = datetime(2026, 4, 28, 8, 0, tzinfo=timezone.utc)
    webkit_us = int(target.timestamp() * 1_000_000) + _WEBKIT_EPOCH_OFFSET_US
    db_path = tmp_path / "webkit.db"
    _make_db(
        db_path,
        [
            ("CREATE TABLE messages (id INTEGER PRIMARY KEY, time INTEGER, content TEXT)", None),
            ("INSERT INTO messages(id, time, content) VALUES (1, ?, ?)", (webkit_us, "webkit ts")),
        ],
    )

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    _approve_sqlite(store, db_path, app_hint="ChromeLike")
    source = ApprovedSqliteSource(approval_store=store)
    instance = source.discover()[0]

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    assert events[0].time == target


def test_parse_time_value_accepts_iso_string_with_z() -> None:
    parsed = parse_time_value("2026-04-28T09:30:00Z")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.astimezone(timezone.utc) == datetime(2026, 4, 28, 9, 30, tzinfo=timezone.utc)


def test_parse_time_value_treats_naive_iso_as_utc() -> None:
    parsed = parse_time_value("2026-04-28T09:30:00")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.astimezone(timezone.utc) == datetime(2026, 4, 28, 9, 30, tzinfo=timezone.utc)


def test_read_parses_corefoundation_seconds(tmp_path: Path) -> None:
    target = datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc)
    cf_seconds = int(target.timestamp() - _CF_EPOCH_OFFSET_S)
    db_path = tmp_path / "cf.db"
    _make_db(
        db_path,
        [
            ("CREATE TABLE events (id INTEGER PRIMARY KEY, time INTEGER, message TEXT)", None),
            ("INSERT INTO events(id, time, message) VALUES (1, ?, ?)", (cf_seconds, "cf ts")),
        ],
    )

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    _approve_sqlite(store, db_path, app_hint="SafariLike")
    source = ApprovedSqliteSource(approval_store=store)
    instance = source.discover()[0]

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    assert events[0].time == target
