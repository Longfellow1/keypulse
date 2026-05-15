from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from keypulse.health.product_delivery import evaluate_delivery_health
from keypulse.store.db import init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


def _seed_event(source: str, ts_start: str) -> None:
    insert_raw_event(
        RawEvent(
            source=source,
            event_type=f"{source}_event",
            ts_start=ts_start,
            speaker="user",
        )
    )


def test_delivery_health_flags_missing_run_record_after_cutoff(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_event("keyboard_chunk", "2026-05-15T10:00:00+08:00")
    _seed_event("clipboard", "2026-05-15T10:10:00+08:00")
    _seed_event("window", "2026-05-15T10:20:00+08:00")

    result = evaluate_delivery_health(
        now_local=datetime(2026, 5, 15, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    assert any(item.source == "daily" and item.level == "critical" for item in result.alerts)
    assert result.run_record_ok is False


def test_delivery_health_flags_degraded_run_record(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    records_dir = tmp_path / ".keypulse" / "run_records"
    records_dir.mkdir(parents=True, exist_ok=True)
    records_dir.joinpath("2026-05-15.json").write_text(
        json.dumps(
            {
                "date": "2026-05-15",
                "stage_status": {"load_rows": "ok", "persist": "failed"},
                "degraded_reason": "persist_failed",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_event("keyboard_chunk", "2026-05-15T10:00:00+08:00")
    _seed_event("clipboard", "2026-05-15T10:10:00+08:00")
    _seed_event("window", "2026-05-15T10:20:00+08:00")

    result = evaluate_delivery_health(
        now_local=datetime(2026, 5, 15, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    assert any(item.source == "daily" and item.level == "warn" for item in result.alerts)
    assert result.run_record_ok is False


def test_delivery_health_ok_when_run_record_and_watchers_ready(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    records_dir = tmp_path / ".keypulse" / "run_records"
    records_dir.mkdir(parents=True, exist_ok=True)
    records_dir.joinpath("2026-05-15.json").write_text(
        json.dumps(
            {
                "date": "2026-05-15",
                "stage_status": {"load_rows": "ok", "persist": "ok"},
                "degraded_reason": "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_event("keyboard_chunk", "2026-05-15T10:00:00+08:00")
    _seed_event("clipboard", "2026-05-15T10:10:00+08:00")
    _seed_event("window", "2026-05-15T10:20:00+08:00")

    result = evaluate_delivery_health(
        now_local=datetime(2026, 5, 15, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    assert not [item for item in result.alerts if item.source == "daily"]
    assert result.run_record_ok is True
