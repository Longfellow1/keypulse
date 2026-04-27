from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from keypulse.pipeline.hourly import (
    aggregate_hourly_events,
    build_hourly_prompt,
    parse_json_payload,
    refresh_hourly_summaries,
)
from keypulse.store.db import init_db


def _insert_raw_event(
    db_path: Path,
    *,
    ts_start: str,
    app_name: str,
    content_text: str,
    event_type: str = "keyboard_chunk_capture",
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO raw_events (
            source, event_type, ts_start, ts_end, app_name, window_title,
            process_name, content_text, content_hash, metadata_json,
            sensitivity_level, skipped_reason, session_id, speaker,
            semantic_weight, user_present, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "manual",
            event_type,
            ts_start,
            ts_start,
            app_name,
            f"{app_name} window",
            app_name,
            content_text,
            f"hash-{ts_start}-{app_name}",
            "{}",
            0,
            "",
            "",
            "system",
            1.0,
            1,
            ts_start,
        ),
    )
    conn.commit()
    conn.close()


class _FakeGateway:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def render(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def test_aggregate_hourly_events_compacts_5min_buckets():
    activities = aggregate_hourly_events(
        [
            {
                "ts_start": "2026-04-25T09:01:00+00:00",
                "app_name": "Chrome",
                "window_title": "A",
                "content_text": "first note",
                "event_type": "keyboard_chunk_capture",
            },
            {
                "ts_start": "2026-04-25T09:03:00+00:00",
                "app_name": "Chrome",
                "window_title": "A",
                "content_text": "second note",
                "event_type": "clipboard_copy",
            },
            {
                "ts_start": "2026-04-25T09:11:00+00:00",
                "app_name": "Safari",
                "window_title": "B",
                "content_text": "research note",
                "event_type": "ax_text_capture",
            },
        ]
    )

    assert len(activities) == 2
    assert activities[0]["ts"].endswith("09:00")
    assert activities[0]["samples"][0] == "first note"
    assert activities[1]["app"] == "Safari"

    prompt = build_hourly_prompt("2026-04-25", 9, activities)
    assert "7 个动机假设" in prompt
    assert len(prompt) < 10000


def test_parse_json_payload_accepts_markdown_fences_and_extraneous_text():
    payload = parse_json_payload(
        "Here you go:\n```json\n{\"ok\": true, \"value\": 3}\n```\nThanks"
    )

    assert payload == {"ok": True, "value": 3}


def test_refresh_hourly_summaries_is_idempotent(tmp_path):
    db_path = tmp_path / "keypulse.db"
    init_db(db_path)
    _insert_raw_event(
        db_path,
        ts_start="2026-04-25T09:10:00+00:00",
        app_name="Chrome",
        content_text="looking up the design",
    )
    _insert_raw_event(
        db_path,
        ts_start="2026-04-25T09:37:00+00:00",
        app_name="VSCode",
        content_text="editing pipeline code",
    )

    gateway = _FakeGateway(
        json.dumps(
            {
                "hour": "2026-04-25T09",
                "topics": ["design", "code"],
                "tools": ["Chrome", "VSCode"],
                "people": [],
                "motive_hints": {
                    "create": 0.5,
                    "understand": 0.6,
                    "decide": 0.3,
                    "maintain": 0.7,
                    "communicate": 0.0,
                    "transact": 0.0,
                    "leisure": 0.0,
                },
                "evidence_refs": [1, 2],
                "summary_zh": "在研究设计并修改管线代码",
            }
        )
    )

    generated = refresh_hourly_summaries(
        db_path,
        "2026-04-25",
        gateway,
        until_hour=10,
    )
    assert len(generated) == 1
    assert gateway.prompts and "09:00" in gateway.prompts[0]

    generated_again = refresh_hourly_summaries(
        db_path,
        "2026-04-25",
        gateway,
        until_hour=10,
    )
    assert generated_again == []
    assert len(gateway.prompts) == 1

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT payload_json FROM hourly_summaries WHERE date=? AND hour=?",
        ("2026-04-25", 9),
    ).fetchone()
    conn.close()

    assert row is not None
    payload = json.loads(row[0])
    assert payload["summary_zh"] == "在研究设计并修改管线代码"
