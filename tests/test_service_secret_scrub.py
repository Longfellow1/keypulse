from __future__ import annotations

import sqlite3

import pytest

from penpal.services.secret_scrub import ScrubModeError, scrub_secrets


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE raw_events (id INTEGER PRIMARY KEY, content_text TEXT)")
    conn.execute("INSERT INTO raw_events (content_text) VALUES (?)", ("token=synthetic_example_value",))
    conn.execute("INSERT INTO raw_events (content_text) VALUES (?)", ("ordinary synthetic note",))
    conn.commit()
    return conn


def test_scrub_secrets_dry_run_returns_changes_without_writing():
    conn = _connection()

    result = scrub_secrets(conn, dry_run=True)

    assert result.applied is False
    assert len(result.changes) == 1
    assert result.changes[0].before == "token=synthetic_example_value"
    assert result.changes[0].after == "[REDACTED]"
    assert conn.execute("SELECT content_text FROM raw_events WHERE id = 1").fetchone()[0] == "token=synthetic_example_value"
    conn.close()


def test_scrub_secrets_apply_persists_redaction():
    conn = _connection()

    result = scrub_secrets(conn, dry_run=False, apply=True)

    assert result.applied is True
    assert len(result.changes) == 1
    assert conn.execute("SELECT content_text FROM raw_events WHERE id = 1").fetchone()[0] == "[REDACTED]"
    assert conn.execute("SELECT content_text FROM raw_events WHERE id = 2").fetchone()[0] == "ordinary synthetic note"
    conn.close()


def test_scrub_secrets_conflicting_modes_are_rejected():
    conn = _connection()

    with pytest.raises(ScrubModeError):
        scrub_secrets(conn, dry_run=True, apply=True)

    assert conn.execute("SELECT content_text FROM raw_events WHERE id = 1").fetchone()[0] == "token=synthetic_example_value"
    conn.close()


def test_scrub_secrets_defaults_to_dry_run_when_neither_mode_is_selected():
    conn = _connection()

    result = scrub_secrets(conn, dry_run=False, apply=False)

    assert result.defaulted_to_dry_run is True
    assert result.dry_run is True
    assert result.applied is False
    assert conn.execute("SELECT content_text FROM raw_events WHERE id = 1").fetchone()[0] == "token=synthetic_example_value"
    conn.close()
