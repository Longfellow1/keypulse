from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from keypulse.health.product_delivery import evaluate_delivery_health
from keypulse.store.db import init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event, set_state


def _seed_event(source: str, ts_start: str) -> None:
    insert_raw_event(
        RawEvent(
            source=source,
            event_type=f"{source}_event",
            ts_start=ts_start,
            speaker="user",
        )
    )


def _healthy_watcher(name: str) -> dict:
    return {
        "name": name,
        "running": True,
        "paused": False,
        "crashes": 0,
        "last_error": None,
        "gave_up": False,
        "heartbeat_revivals": 0,
        "heartbeat_gave_up": False,
        "last_emit_age_sec": 60.0,
        "last_beat_age_sec": 1.0,
        "heartbeat_timeout_sec": 1800.0,
    }


def _seed_capture_runtime(watchers: dict[str, dict]) -> None:
    set_state(
        "capture_runtime",
        json.dumps({"watchers": watchers}, ensure_ascii=False),
    )


def _seed_all_healthy_watchers() -> None:
    """Seed every WATCHER_TIERS source as healthy so default cases stay quiet."""
    from keypulse.observability.watcher_tiers import WATCHER_TIERS

    _seed_capture_runtime({name: _healthy_watcher(name) for name in WATCHER_TIERS})


def test_delivery_health_flags_missing_run_record_after_cutoff(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_all_healthy_watchers()
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
    _seed_all_healthy_watchers()
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
    _seed_all_healthy_watchers()
    _seed_event("keyboard_chunk", "2026-05-15T10:00:00+08:00")
    _seed_event("clipboard", "2026-05-15T10:10:00+08:00")
    _seed_event("window", "2026-05-15T10:20:00+08:00")

    result = evaluate_delivery_health(
        now_local=datetime(2026, 5, 15, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    assert not [item for item in result.alerts if item.source == "daily"]
    assert result.run_record_ok is True


def test_delivery_health_silent_when_user_idle_but_watchers_healthy(tmp_path, monkeypatch):
    """Zero emits across all sources should NOT alert when watchers heartbeat OK."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    records_dir = tmp_path / ".keypulse" / "run_records"
    records_dir.mkdir(parents=True, exist_ok=True)
    records_dir.joinpath("2026-05-15.json").write_text(
        json.dumps({"date": "2026-05-15", "stage_status": {"persist": "ok"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_all_healthy_watchers()

    result = evaluate_delivery_health(
        now_local=datetime(2026, 5, 15, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    watcher_alerts = [item for item in result.alerts if item.source.startswith("watcher:")]
    assert watcher_alerts == []


def test_delivery_health_flags_watcher_beat_stale(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    records_dir = tmp_path / ".keypulse" / "run_records"
    records_dir.mkdir(parents=True, exist_ok=True)
    records_dir.joinpath("2026-05-15.json").write_text(
        json.dumps({"date": "2026-05-15", "stage_status": {"persist": "ok"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    stale = _healthy_watcher("keyboard_chunk")
    stale["last_beat_age_sec"] = 4000.0  # > 2x heartbeat_timeout_sec (1800)
    _seed_capture_runtime({"keyboard_chunk": stale})

    result = evaluate_delivery_health(
        now_local=datetime(2026, 5, 15, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    alerts = [item for item in result.alerts if item.source == "watcher:keyboard_chunk"]
    assert len(alerts) == 1
    assert alerts[0].level == "critical"
    assert "心跳" in alerts[0].message


def test_delivery_health_flags_watcher_last_error(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    records_dir = tmp_path / ".keypulse" / "run_records"
    records_dir.mkdir(parents=True, exist_ok=True)
    records_dir.joinpath("2026-05-15.json").write_text(
        json.dumps({"date": "2026-05-15", "stage_status": {"persist": "ok"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    broken = _healthy_watcher("clipboard")
    broken["last_error"] = "permission denied"
    _seed_capture_runtime({"clipboard": broken})

    result = evaluate_delivery_health(
        now_local=datetime(2026, 5, 15, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    alerts = [item for item in result.alerts if item.source == "watcher:clipboard"]
    assert len(alerts) == 1
    assert alerts[0].level == "critical"
    assert "permission denied" in alerts[0].message


def test_delivery_health_silent_for_unregistered_watcher(tmp_path, monkeypatch):
    """camera/browser_history/etc not in runtime payload should not alert."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    records_dir = tmp_path / ".keypulse" / "run_records"
    records_dir.mkdir(parents=True, exist_ok=True)
    records_dir.joinpath("2026-05-15.json").write_text(
        json.dumps({"date": "2026-05-15", "stage_status": {"persist": "ok"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    # Only seed core watchers — camera / browser_history / ax_text absent
    _seed_capture_runtime({name: _healthy_watcher(name) for name in ("keyboard_chunk", "clipboard", "browser_url", "window", "idle")})

    result = evaluate_delivery_health(
        now_local=datetime(2026, 5, 15, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        now_utc=datetime(2026, 5, 15, 11, 0, tzinfo=timezone.utc),
    )

    optional_alerts = [
        item for item in result.alerts
        if item.source in {"watcher:camera", "watcher:browser_history", "watcher:ax_text"}
    ]
    assert optional_alerts == []
