"""D32: KeyPulse never deletes user data automatically.

Regression guard for the old startup retention pass, which silently deleted raw_events
older than retention_days (default 30). Explicit deletion stays available via `keypulse purge`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from keypulse.capture.manager import CaptureManager
from keypulse.config import Config
from keypulse.store.db import close, get_conn, init_db
from keypulse.store.models import RawEvent, SearchDoc
from keypulse.store.repository import insert_raw_event, insert_search_doc


def _counts(conn) -> tuple[int, int]:
    raw = conn.execute("SELECT COUNT(*) FROM raw_events").fetchone()[0]
    docs = conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0]
    return raw, docs


def test_daemon_start_and_stop_never_delete_400_day_old_history(tmp_path):
    close()
    db_path = tmp_path / "keypulse.db"
    init_db(db_path)

    ancient = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    for i in range(3):
        insert_raw_event(
            RawEvent(
                source="manual",
                event_type="manual_save",
                ts_start=f"{ancient}#{i}",
                created_at=ancient,
                content_text=f"old note {i}",
            )
        )
    insert_search_doc(
        SearchDoc(ref_type="clipboard", ref_id="old-clip", body="old clipboard", created_at=ancient)
    )
    before = _counts(get_conn())
    assert before == (3, 1)

    config = Config.model_validate(
        {
            "app": {"db_path": str(db_path), "retention_days": 30},
            "watchers": {
                "window": False, "idle": False, "clipboard": False, "manual": False,
                "keyboard_chunk": False, "browser": False, "ax_text": False, "ocr": False,
            },
        }
    )
    assert config.app.retention_days == 30  # still parsed for old config files, but ignored

    manager = CaptureManager(config)
    manager.start()
    manager.stop()

    assert _counts(get_conn()) == before
    close()


def test_retention_pass_is_removed_from_store():
    import keypulse.store.repository as repository

    assert not hasattr(repository, "apply_retention")
