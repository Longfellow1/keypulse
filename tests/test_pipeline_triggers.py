"""
Tests for keypulse.pipeline.triggers: fail-closed LLM-trigger decision module.
8 test cases covering T1, T2, T3 rules, error handling, and record_trigger safety.
"""

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from keypulse.pipeline.triggers import should_trigger, record_trigger


@pytest.fixture
def tmp_db():
    """In-memory SQLite DB for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield db_path


def seed_raw_events(db_path: Path, speaker: str, content_text: str, ts_offset_hours: float):
    """Helper: insert a raw_events row with given speaker and content."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS raw_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            speaker TEXT NOT NULL,
            content_text TEXT,
            ts_start TEXT NOT NULL
        )
        """
    )

    now = datetime.utcnow()
    ts = (now - timedelta(hours=ts_offset_hours)).isoformat()
    cursor.execute(
        "INSERT INTO raw_events (speaker, content_text, ts_start) VALUES (?, ?, ?)",
        (speaker, content_text, ts),
    )
    conn.commit()
    conn.close()


def seed_trigger_log(db_path: Path, kind: str, outcome: str, ts_offset_hours: float):
    """Helper: insert a trigger log entry."""
    now = datetime.utcnow()
    ts = (now - timedelta(hours=ts_offset_hours)).isoformat()

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_trigger_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            outcome TEXT NOT NULL,
            note TEXT DEFAULT ''
        )
        """
    )

    cursor.execute(
        "INSERT INTO llm_trigger_log (kind, ts_utc, outcome, note) VALUES (?, ?, ?, ?)",
        (kind, ts, outcome, ""),
    )
    conn.commit()
    conn.close()


class TestT2AlwaysAllows:
    """T2 always allows even with no activity."""

    def test_t2_allowed_no_activity(self, tmp_db):
        now = datetime.utcnow()
        allowed, reason = should_trigger("T2", now=now, db_path=tmp_db, cfg={})
        assert allowed is True
        assert "T2:always_allowed" in reason


class TestT1Activity:
    """T1 requires user activity in last 5h."""

    def test_t1_denied_no_activity(self, tmp_db):
        """T1 denied when no user activity in last 5h."""
        now = datetime.utcnow()
        allowed, reason = should_trigger("T1", now=now, db_path=tmp_db, cfg={})
        assert allowed is False
        assert "T1:no_activity_5h" in reason

    def test_t1_allowed_with_activity(self, tmp_db):
        """T1 allowed when ≥50 chars of user activity in last 5h."""
        seed_raw_events(tmp_db, "user", "a" * 50, ts_offset_hours=2.0)
        now = datetime.utcnow()
        allowed, reason = should_trigger("T1", now=now, db_path=tmp_db, cfg={})
        assert allowed is True
        assert "T1:activity_ok" in reason


class TestT3Caps:
    """T3 has per-5h cap and per-1h global cap."""

    def test_t3_denied_3_in_5h(self, tmp_db):
        """T3 denied when 3 prior T3 triggers in last 5h."""
        seed_trigger_log(tmp_db, "T3", "allowed", 4.0)
        seed_trigger_log(tmp_db, "T3", "allowed", 3.0)
        seed_trigger_log(tmp_db, "T3", "allowed", 2.0)

        now = datetime.utcnow()
        allowed, reason = should_trigger("T3", now=now, db_path=tmp_db, cfg={})
        assert allowed is False
        assert "T3:cap_5h" in reason

    def test_t3_denied_1_in_1h_global_cap(self, tmp_db):
        """T3 denied when 1 prior T3 trigger in last 1h (global cap)."""
        seed_trigger_log(tmp_db, "T3", "allowed", 0.5)

        now = datetime.utcnow()
        allowed, reason = should_trigger("T3", now=now, db_path=tmp_db, cfg={})
        assert allowed is False
        assert "T3:global_cap_1h" in reason

    def test_t3_allowed_old_and_sparse(self, tmp_db):
        """T3 allowed when last T3 was 2h ago and only 1 total in last 5h."""
        seed_trigger_log(tmp_db, "T3", "allowed", 2.0)

        now = datetime.utcnow()
        allowed, reason = should_trigger("T3", now=now, db_path=tmp_db, cfg={})
        assert allowed is True
        assert "T3:allowed" in reason


class TestErrorHandling:
    """Unknown kind and error resilience."""

    def test_unknown_kind(self, tmp_db):
        """Unknown kind returns (False, 'error:unknown_kind')."""
        now = datetime.utcnow()
        allowed, reason = should_trigger("UNKNOWN", now=now, db_path=tmp_db, cfg={})
        assert allowed is False
        assert "error:unknown_kind" in reason


class TestRecordTrigger:
    """record_trigger safety and idempotence."""

    def test_record_trigger_idempotent(self, tmp_db):
        """record_trigger is safe across repeated calls."""
        now = datetime.utcnow()

        record_trigger("T1", now=now, db_path=tmp_db, outcome="allowed")
        record_trigger("T1", now=now, db_path=tmp_db, outcome="allowed")
        record_trigger("T2", now=now, db_path=tmp_db, outcome="allowed", note="test")

        conn = sqlite3.connect(str(tmp_db))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM llm_trigger_log")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 3


class TestIntegration:
    """End-to-end scenarios."""

    def test_t1_workflow(self, tmp_db):
        """T1: check activity, record decision."""
        now = datetime.utcnow()

        # Start with no activity: denied
        allowed, reason = should_trigger("T1", now=now, db_path=tmp_db, cfg={})
        assert allowed is False
        record_trigger("T1", now=now, db_path=tmp_db, outcome=f"skipped:{reason}")

        # Add activity
        seed_raw_events(tmp_db, "user", "test input" * 10, ts_offset_hours=1.0)

        # Now allowed
        allowed, reason = should_trigger("T1", now=now, db_path=tmp_db, cfg={})
        assert allowed is True
        record_trigger("T1", now=now, db_path=tmp_db, outcome="allowed")

        conn = sqlite3.connect(str(tmp_db))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM llm_trigger_log WHERE outcome LIKE 'skipped:%'")
        skipped_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM llm_trigger_log WHERE outcome = 'allowed'")
        allowed_count = cursor.fetchone()[0]
        conn.close()

        assert skipped_count == 1
        assert allowed_count == 1
