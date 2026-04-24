from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from keypulse.pipeline.evidence import EvidenceUnit
from keypulse.pipeline.signals import (
    BrowserSignal,
    FsSignal,
    collect_browser_signals,
    collect_filesystem_signals,
    enrich_unit_with_signals,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = datetime(2026, 4, 23, 9, 0, 0, tzinfo=timezone.utc)
_END = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)


def _make_unit(what: str = "test task") -> EvidenceUnit:
    return EvidenceUnit(
        ts_start=_BASE,
        ts_end=_END,
        where="VSCode",
        who="user",
        what=what,
        evidence_refs=[],
        semantic_weight=0.5,
        machine_online=True,
        confidence=0.5,
    )


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE raw_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_start TEXT NOT NULL,
            ts_end TEXT,
            text TEXT,
            app_name TEXT,
            source TEXT,
            speaker TEXT,
            semantic_weight REAL DEFAULT 0.5
        )
        """
    )
    conn.commit()
    conn.close()
    return db


def _insert_browser_event(db: Path, ts: str, title: str, url: str = "", app: str = "Safari") -> None:
    conn = sqlite3.connect(str(db))
    payload = json.dumps({"title": title, "url": url})
    conn.execute(
        "INSERT INTO raw_events (ts_start, text, app_name, source, speaker) VALUES (?, ?, ?, ?, ?)",
        (ts, payload, app, "browser", "system"),
    )
    conn.commit()
    conn.close()


def _set_mtime(path: Path, dt: datetime) -> None:
    ts = dt.timestamp()
    os.utime(str(path), (ts, ts))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_collect_fs_signals_empty_window(tmp_path):
    """No files with mtime in an empty future window."""
    watch = tmp_path / "watch"
    watch.mkdir()
    f = watch / "file.py"
    f.write_text("hello")
    # mtime is now; window is far in the future
    future_start = datetime(2099, 1, 1, tzinfo=timezone.utc)
    future_end = datetime(2099, 1, 2, tzinfo=timezone.utc)
    signals = collect_filesystem_signals(future_start, future_end, watch_paths=[watch])
    assert signals == []


def test_collect_fs_signals_filters_hidden_and_excluded(tmp_path):
    """Hidden files and excluded directories are omitted; visible files pass."""
    watch = tmp_path / "watch"
    watch.mkdir()

    # Hidden file at top level
    hidden = watch / ".hidden"
    hidden.write_text("secret")

    # Excluded directory
    node_mod = watch / "node_modules"
    node_mod.mkdir()
    (node_mod / "x.js").write_text("module")

    # Visible file that should be returned
    real = watch / "real.py"
    real.write_text("code")

    # Set all mtimes inside window
    window_ts = datetime(2026, 4, 23, 8, 30, 0, tzinfo=timezone.utc)
    since = datetime(2026, 4, 23, 8, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 4, 23, 9, 0, 0, tzinfo=timezone.utc)
    for p in [hidden, node_mod / "x.js", real]:
        _set_mtime(p, window_ts)

    signals = collect_filesystem_signals(since, until, watch_paths=[watch])
    basenames = [s.basename for s in signals]
    assert "real.py" in basenames
    assert ".hidden" not in basenames
    assert "x.js" not in basenames


def test_collect_fs_signals_respects_time_window(tmp_path):
    """Files with mtime outside the window are excluded."""
    watch = tmp_path / "watch"
    watch.mkdir()
    f = watch / "old.py"
    f.write_text("old")

    outside = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    _set_mtime(f, outside)

    since = datetime(2026, 4, 23, 8, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 4, 23, 9, 0, 0, tzinfo=timezone.utc)
    signals = collect_filesystem_signals(since, until, watch_paths=[watch])
    assert signals == []


def test_collect_browser_signals_aggregates_duplicates(tmp_path):
    """Three consecutive rows with same title collapse to 1 BrowserSignal."""
    db = _make_db(tmp_path)
    for i in range(3):
        ts = f"2026-04-23T09:0{i}:00"
        _insert_browser_event(db, ts, "GitHub - keypulse")

    since = datetime(2026, 4, 23, 9, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)
    signals = collect_browser_signals(since, until, db_path=db)
    assert len(signals) == 1
    assert signals[0].title == "GitHub - keypulse"


def test_enrich_unit_appends_fs_and_browser(tmp_path):
    """enrich_unit_with_signals appends fs and browser text to what."""
    unit = _make_unit("coding session")

    fs = [
        FsSignal(
            path="/project/app.py",
            basename="app.py",
            mtime=datetime(2026, 4, 23, 9, 30, 0, tzinfo=timezone.utc),
            size=100,
            ext=".py",
        )
    ]
    browser = [
        BrowserSignal(
            title="Python docs",
            url="https://docs.python.org",
            app="Safari",
            ts=datetime(2026, 4, 23, 9, 45, 0, tzinfo=timezone.utc),
        )
    ]

    enriched = enrich_unit_with_signals(unit, fs_signals=fs, browser_signals=browser)
    assert "改了" in enriched.what
    assert "app.py" in enriched.what
    assert "浏览了" in enriched.what
    assert "Python docs" in enriched.what
    # original not mutated
    assert enriched.what != unit.what
    assert unit.what == "coding session"


def test_enrich_unit_truncates_over_3(tmp_path):
    """Only the first 3 fs signals are appended when more than 3 are given."""
    unit = _make_unit("many files")

    fs = [
        FsSignal(
            path=f"/project/file{i}.py",
            basename=f"file{i}.py",
            mtime=datetime(2026, 4, 23, 9, i + 1, 0, tzinfo=timezone.utc),
            size=50,
            ext=".py",
        )
        for i in range(5)
    ]

    enriched = enrich_unit_with_signals(unit, fs_signals=fs, browser_signals=[])
    # Only first 3 basenames should appear
    for i in range(3):
        assert f"file{i}.py" in enriched.what
    # file3.py and file4.py should NOT appear
    assert "file3.py" not in enriched.what
    assert "file4.py" not in enriched.what


def test_enrich_unit_only_includes_signals_in_time_window():
    """Signals outside the unit time window are not appended."""
    unit = _make_unit("narrow window")

    outside_fs = [
        FsSignal(
            path="/project/old.py",
            basename="old.py",
            mtime=datetime(2026, 4, 22, 8, 0, 0, tzinfo=timezone.utc),  # day before
            size=10,
            ext=".py",
        )
    ]
    outside_browser = [
        BrowserSignal(
            title="Old page",
            url=None,
            app="Safari",
            ts=datetime(2026, 4, 22, 8, 0, 0, tzinfo=timezone.utc),
        )
    ]

    enriched = enrich_unit_with_signals(unit, fs_signals=outside_fs, browser_signals=outside_browser)
    assert enriched.what == unit.what
