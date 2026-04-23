"""Tests for T2 and T3 daemon startup/idle triggers."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from keypulse.app import _t3_tick, _spawn_t2_trigger


class TestT3Tick:
    """Test _t3_tick pure function logic."""

    @pytest.fixture
    def temp_db(self, tmp_path: Path) -> Path:
        """Create a minimal test DB with raw_events table."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE raw_events (
                id INTEGER PRIMARY KEY,
                ts_utc TEXT NOT NULL,
                speaker TEXT NOT NULL,
                content_text TEXT
            )
            """
        )
        conn.commit()
        conn.close()
        return db_path

    def test_t3_active_to_inactive_transition_fires_sync(self, temp_db: Path):
        """
        T3 should fire sync on active→inactive transition if idle >= 10min.
        """
        # Setup: insert event that makes window active (>= 50 chars)
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        window_start = (now - timedelta(minutes=4)).isoformat()
        cursor.execute(
            "INSERT INTO raw_events (ts_utc, speaker, content_text) VALUES (?, ?, ?)",
            (window_start, "user", "x" * 100),
        )
        conn.commit()
        conn.close()

        # Tick 1: active (100 chars in window)
        state = {"was_active": False}
        sync_called = []

        def mock_sync():
            sync_called.append(True)

        def mock_should_trigger(kind: str):
            return (True, "T3:allowed")

        def mock_record(kind: str, outcome: str):
            pass

        def mock_idle():
            return 700.0  # 700 seconds = 11.67 minutes > 10min

        state = _t3_tick(
            state,
            now=now,
            db_path=temp_db,
            sync_fn=mock_sync,
            should_trigger_fn=mock_should_trigger,
            record_trigger_fn=mock_record,
            idle_seconds_fn=mock_idle,
        )
        assert state["was_active"] is True
        assert len(sync_called) == 0  # Still active, no transition

        # Tick 2: inactive (delete event, now <50 chars)
        conn = sqlite3.connect(str(temp_db))
        conn.execute("DELETE FROM raw_events")
        conn.commit()
        conn.close()

        state = _t3_tick(
            state,
            now=now,
            db_path=temp_db,
            sync_fn=mock_sync,
            should_trigger_fn=mock_should_trigger,
            record_trigger_fn=mock_record,
            idle_seconds_fn=mock_idle,
        )
        assert state["was_active"] is False
        assert len(sync_called) == 1  # Transition occurred, sync fired

    def test_t3_stays_active_does_not_fire(self, temp_db: Path):
        """
        T3 should NOT fire if activity window stays above 50 chars.
        """
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Insert 60 chars
        window_start = (now - timedelta(minutes=4)).isoformat()
        cursor.execute(
            "INSERT INTO raw_events (ts_utc, speaker, content_text) VALUES (?, ?, ?)",
            (window_start, "user", "x" * 60),
        )
        conn.commit()
        conn.close()

        sync_called = []

        def mock_sync():
            sync_called.append(True)

        def mock_should_trigger(kind: str):
            return (True, "T3:allowed")

        def mock_record(kind: str, outcome: str):
            pass

        def mock_idle():
            return 700.0

        state = {"was_active": True}

        # Tick 1: still active
        state = _t3_tick(
            state,
            now=now,
            db_path=temp_db,
            sync_fn=mock_sync,
            should_trigger_fn=mock_should_trigger,
            record_trigger_fn=mock_record,
            idle_seconds_fn=mock_idle,
        )
        assert state["was_active"] is True
        assert len(sync_called) == 0

        # Tick 2: still active
        state = _t3_tick(
            state,
            now=now,
            db_path=temp_db,
            sync_fn=mock_sync,
            should_trigger_fn=mock_should_trigger,
            record_trigger_fn=mock_record,
            idle_seconds_fn=mock_idle,
        )
        assert state["was_active"] is True
        assert len(sync_called) == 0

    def test_t3_respects_should_trigger_veto(self, temp_db: Path):
        """
        T3 should NOT fire sync if should_trigger returns False.
        """
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Insert event, then delete to cause transition
        window_start = (now - timedelta(minutes=4)).isoformat()
        cursor.execute(
            "INSERT INTO raw_events (ts_utc, speaker, content_text) VALUES (?, ?, ?)",
            (window_start, "user", "x" * 100),
        )
        conn.commit()
        conn.close()

        sync_called = []
        record_called = []

        def mock_sync():
            sync_called.append(True)

        def mock_should_trigger(kind: str):
            return (False, "T3:cap_1h")  # Veto

        def mock_record(kind: str, outcome: str):
            record_called.append((kind, outcome))

        def mock_idle():
            return 700.0

        state = {"was_active": True}

        # Tick: transition to inactive, but should_trigger vetoes
        conn = sqlite3.connect(str(temp_db))
        conn.execute("DELETE FROM raw_events")
        conn.commit()
        conn.close()

        state = _t3_tick(
            state,
            now=now,
            db_path=temp_db,
            sync_fn=mock_sync,
            should_trigger_fn=mock_should_trigger,
            record_trigger_fn=mock_record,
            idle_seconds_fn=mock_idle,
        )
        assert state["was_active"] is False
        assert len(sync_called) == 0  # Not called due to veto
        assert ("T3", "skipped:T3:cap_1h") in record_called

    def test_t3_ignores_transition_if_idle_short(self, temp_db: Path):
        """
        T3 should NOT fire if idle duration < 10min, even on transition.
        """
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Setup active->inactive
        window_start = (now - timedelta(minutes=4)).isoformat()
        cursor.execute(
            "INSERT INTO raw_events (ts_utc, speaker, content_text) VALUES (?, ?, ?)",
            (window_start, "user", "x" * 100),
        )
        conn.commit()
        conn.close()

        sync_called = []

        def mock_sync():
            sync_called.append(True)

        def mock_should_trigger(kind: str):
            return (True, "T3:allowed")

        def mock_record(kind: str, outcome: str):
            pass

        def mock_idle():
            return 300.0  # 5 minutes < 10min

        state = {"was_active": True}

        # Tick 1: active
        state = _t3_tick(
            state,
            now=now,
            db_path=temp_db,
            sync_fn=mock_sync,
            should_trigger_fn=mock_should_trigger,
            record_trigger_fn=mock_record,
            idle_seconds_fn=mock_idle,
        )
        assert state["was_active"] is True
        assert len(sync_called) == 0

        # Tick 2: transition, but idle only 5min
        conn = sqlite3.connect(str(temp_db))
        conn.execute("DELETE FROM raw_events")
        conn.commit()
        conn.close()

        state = _t3_tick(
            state,
            now=now,
            db_path=temp_db,
            sync_fn=mock_sync,
            should_trigger_fn=mock_should_trigger,
            record_trigger_fn=mock_record,
            idle_seconds_fn=mock_idle,
        )
        assert state["was_active"] is False
        assert len(sync_called) == 0  # No fire due to short idle

    def test_t3_swallows_sync_exception(self, temp_db: Path):
        """
        T3 should catch sync exceptions, log, and record "ran:fail".
        """
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Setup active->inactive
        window_start = (now - timedelta(minutes=4)).isoformat()
        cursor.execute(
            "INSERT INTO raw_events (ts_utc, speaker, content_text) VALUES (?, ?, ?)",
            (window_start, "user", "x" * 100),
        )
        conn.commit()
        conn.close()

        record_called = []

        def mock_sync():
            raise RuntimeError("sync failed")

        def mock_should_trigger(kind: str):
            return (True, "T3:allowed")

        def mock_record(kind: str, outcome: str):
            record_called.append((kind, outcome))

        def mock_idle():
            return 700.0

        state = {"was_active": True}

        # Transition
        conn = sqlite3.connect(str(temp_db))
        conn.execute("DELETE FROM raw_events")
        conn.commit()
        conn.close()

        # Should not raise, despite sync failure
        state = _t3_tick(
            state,
            now=now,
            db_path=temp_db,
            sync_fn=mock_sync,
            should_trigger_fn=mock_should_trigger,
            record_trigger_fn=mock_record,
            idle_seconds_fn=mock_idle,
        )
        assert state["was_active"] is False
        assert ("T3", "ran:fail") in record_called


class TestT2Spawn:
    """Test T2 daemon startup trigger wiring."""

    @pytest.fixture
    def temp_db(self, tmp_path: Path) -> Path:
        """Create a minimal test DB."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE raw_events (
                id INTEGER PRIMARY KEY,
                ts_utc TEXT NOT NULL,
                speaker TEXT NOT NULL,
                content_text TEXT
            )
            """
        )
        conn.commit()
        conn.close()
        return db_path

    def test_t2_fires_sync_after_delay(self, tmp_path: Path, temp_db: Path):
        """
        T2 timer should fire after delay, call should_trigger, record, and sync.
        """
        from keypulse.config import Config

        # Create a mock config with db_path_expanded pointing to temp_db
        cfg = MagicMock(spec=Config)
        cfg.db_path_expanded = temp_db

        with patch("keypulse.app._run_obsidian_sync_core") as mock_sync:
            shutdown = threading.Event()
            _spawn_t2_trigger(cfg, shutdown, delay=0.0)
            time.sleep(0.5)  # Let Timer fire

            # Verify sync was called with cfg
            mock_sync.assert_called_once_with(cfg)

        # Verify T2 record in DB
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM llm_trigger_log WHERE kind='T2'")
        count = cursor.fetchone()[0]
        conn.close()
        assert count >= 1
