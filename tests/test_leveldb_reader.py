from __future__ import annotations

import subprocess
import os
from datetime import datetime, timezone
from pathlib import Path

from keypulse.sources.approval import ApprovalStore
from keypulse.sources.discoverers import CandidateSource
from keypulse.sources.plugins.leveldb_reader import LevelDbReaderSource
from keypulse.sources.registry import get_source


def _approve_leveldb(store: ApprovalStore, path: Path, *, app_hint: str = "Cursor") -> str:
    candidate = CandidateSource(
        discoverer="leveldb",
        path=str(path.resolve()),
        app_hint=app_hint,
        schema_signature="leveldb:2files:0.0MB",
        shape="kv_json_blob",
        confidence="high",
    )
    return store.approve(candidate, note="test").candidate_id


def _make_leveldb_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "MANIFEST-000001").write_text("manifest", encoding="utf-8")
    (path / "CURRENT").write_text("000001", encoding="utf-8")


def test_leveldb_reader_source_is_registered() -> None:
    assert get_source("leveldb_reader") is not None


def test_discover_requires_approval(tmp_path: Path) -> None:
    leveldb_dir = tmp_path / "Cursor" / "Local Storage" / "leveldb"
    _make_leveldb_dir(leveldb_dir)
    (leveldb_dir / "000001.ldb").write_bytes(b"x" * 128)
    (leveldb_dir / "000002.ldb").write_bytes(b"x" * 128)

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    source = LevelDbReaderSource(approval_store=store)

    assert source.discover() == []

    candidate_id = _approve_leveldb(store, leveldb_dir, app_hint="Cursor")
    instances = source.discover()

    assert len(instances) == 1
    instance = instances[0]
    assert instance.plugin == "leveldb_reader"
    assert instance.locator == str(leveldb_dir.resolve())
    assert instance.metadata["candidate_id"] == candidate_id
    assert instance.metadata["shape"] == "kv_json_blob"


def test_discover_ignores_rejected_leveldb_candidate(tmp_path: Path) -> None:
    leveldb_dir = tmp_path / "Figma" / "Session Storage"
    _make_leveldb_dir(leveldb_dir)
    (leveldb_dir / "000001.ldb").write_bytes(b"x" * 128)
    (leveldb_dir / "000002.ldb").write_bytes(b"x" * 128)

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    candidate = CandidateSource(
        discoverer="leveldb",
        path=str(leveldb_dir.resolve()),
        app_hint="Figma",
        schema_signature="leveldb:2files:0.0MB",
        shape="kv_json_blob",
        confidence="high",
    )
    store.reject(candidate, reason="no")

    source = LevelDbReaderSource(approval_store=store)
    assert source.discover() == []


def test_read_extracts_kv_json_rows(tmp_path: Path) -> None:
    leveldb_dir = tmp_path / "Cursor" / "Session Storage"
    _make_leveldb_dir(leveldb_dir)
    ldb_path = leveldb_dir / "000001.ldb"
    ldb_path.write_bytes(
        (
            b"noise\x00"
            b"chat:1 {'timestamp': '2026-04-28T10:00:00+00:00', 'type': 'assistant', 'text': 'contact alice@example.com'}\\n"
            b"invalid not-json\\n"
        )
    )
    (leveldb_dir / "000002.ldb").write_bytes(b"x" * 128)
    in_window_ts = datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc).timestamp()
    os.utime(ldb_path, (in_window_ts, in_window_ts))

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    _approve_leveldb(store, leveldb_dir, app_hint="Cursor")

    source = LevelDbReaderSource(approval_store=store)
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
    assert event.source == "leveldb_reader"
    assert event.actor == "assistant"
    assert event.artifact == "Cursor:chat:1"
    assert event.raw_ref.startswith("leveldb_reader:")
    assert "[REDACTED]" in event.intent
    assert event.metadata["shape"] == "kv_json_blob"
    assert event.metadata["candidate_id"]


def test_read_prefers_strings_subprocess_when_available(monkeypatch, tmp_path: Path) -> None:
    leveldb_dir = tmp_path / "Continue" / "Local Storage" / "leveldb"
    _make_leveldb_dir(leveldb_dir)
    ldb_path = leveldb_dir / "000001.ldb"
    ldb_path.write_bytes(b"binary")
    (leveldb_dir / "000002.ldb").write_bytes(b"x" * 128)
    in_window_ts = datetime(2026, 4, 28, 9, 0, tzinfo=timezone.utc).timestamp()
    os.utime(ldb_path, (in_window_ts, in_window_ts))

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    _approve_leveldb(store, leveldb_dir, app_hint="Continue")
    source = LevelDbReaderSource(approval_store=store)
    instance = source.discover()[0]

    def fake_run(command, **kwargs):
        assert command[0] == "strings"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "chat:2 {'timestamp': '2026-04-28T09:30:00+00:00', 'type': 'user', 'text': 'prompt'}\\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("keypulse.sources.plugins.leveldb_reader.subprocess.run", fake_run)

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )
    assert len(events) == 1
    assert events[0].artifact == "Continue:chat:2"
