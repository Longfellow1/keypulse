from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from keypulse.store.db import close, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "migrate_v0_entities.py"


def _insert_row_with_metadata(db_path: Path, metadata: dict) -> None:
    close()
    init_db(db_path)
    event = RawEvent(
        source="browser",
        event_type="browser_tab",
        ts_start="2026-05-06T10:00:00+00:00",
        content_text="visit",
        content_hash=f"h-{len(json.dumps(metadata, ensure_ascii=False))}",
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    )
    insert_raw_event(event)
    close()


def _read_metadata(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT metadata_json FROM raw_events ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    return json.loads(row["metadata_json"])


def test_migrate_v0_entities_dry_run_does_not_write_and_reports_success_rate(tmp_path) -> None:
    db_path = tmp_path / "keypulse.db"
    _insert_row_with_metadata(
        db_path,
        {
            "full_hash": "abc1234def5678",
            "url": "https://example.com/docs?a=1#frag",
        },
    )
    before = _read_metadata(db_path)

    result = subprocess.run(
        [sys.executable, str(_script_path()), "--db", str(db_path), "--dry-run"],
        check=False,
        capture_output=True,
        text=True,
    )
    after = _read_metadata(db_path)

    assert result.returncode == 0
    assert "mode=DRY-RUN" in result.stdout
    assert "success_rate=" in result.stdout
    assert before == after

