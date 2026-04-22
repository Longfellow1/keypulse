from __future__ import annotations

from datetime import datetime, timedelta, timezone

from keypulse.store.db import close, get_conn, init_db
from keypulse.store.models import RawEvent, SearchDoc
from keypulse.store.repository import (
    apply_retention,
    insert_raw_event,
    insert_search_doc,
)


def test_apply_retention_deletes_only_expired_raw_events_and_clipboard_docs(tmp_path):
    close()
    db_path = tmp_path / "keypulse.db"
    init_db(db_path)

    now = datetime.now(timezone.utc)
    expired = (now - timedelta(days=40)).isoformat()
    fresh = (now - timedelta(days=5)).isoformat()

    insert_raw_event(
        RawEvent(
            source="manual",
            event_type="manual_save",
            ts_start=expired,
            created_at=expired,
        )
    )
    insert_raw_event(
        RawEvent(
            source="manual",
            event_type="manual_save",
            ts_start=fresh,
            created_at=fresh,
        )
    )
    insert_search_doc(
        SearchDoc(
            ref_type="clipboard",
            ref_id="old-clipboard",
            body="old clipboard body",
            created_at=expired,
        )
    )
    insert_search_doc(
        SearchDoc(
            ref_type="clipboard",
            ref_id="new-clipboard",
            body="new clipboard body",
            created_at=fresh,
        )
    )
    insert_search_doc(
        SearchDoc(
            ref_type="manual",
            ref_id="old-manual",
            body="old manual body",
            created_at=expired,
        )
    )

    apply_retention(retention_days=30)

    conn = get_conn()
    raw_ids = {
        row["id"]
        for row in conn.execute("SELECT id FROM raw_events ORDER BY id").fetchall()
    }
    doc_refs = {
        (row["ref_type"], row["ref_id"])
        for row in conn.execute(
            "SELECT ref_type, ref_id FROM search_docs ORDER BY id"
        ).fetchall()
    }

    assert raw_ids == {2}
    assert doc_refs == {
        ("clipboard", "new-clipboard"),
        ("manual", "old-manual"),
    }

    close()
