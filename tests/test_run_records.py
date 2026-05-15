from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from keypulse.pipeline.daily_orchestrator import run_daily
from keypulse.pipeline.run_record import RunRecorder
from keypulse.store.db import close, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


def _write_config(tmp_path: Path) -> None:
    config_dir = tmp_path / ".keypulse"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath("config.toml").write_text(
        """
[model]
active_profile = "cloud-only"

[model.cloud]
kind = "openai_compatible"
base_url = "https://example.test/v1"
model = "deepseek-chat"
tier = ""

[model.local]
kind = "lm_studio"
base_url = "http://127.0.0.1:1234"
model = "qwen2.5-7b"
tier = ""
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _patch_home_and_sink(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_topic_anchor._DEFAULT_PATH",
        tmp_path / ".keypulse" / "weekly-anchor.json",
    )
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )


def _insert_event(source: str, ts_start: str, content: str) -> None:
    insert_raw_event(
        RawEvent(
            source=source,
            event_type=f"{source}_event",
            ts_start=ts_start,
            app_name="Codex",
            window_title="KeyPulse",
            content_text=content,
            metadata_json=json.dumps({"entities": {"session_id": source}}, ensure_ascii=False),
            speaker="user",
        )
    )


def _record_path(tmp_path: Path, date_str: str = "2026-05-01") -> Path:
    return tmp_path / ".keypulse" / "run_records" / f"{date_str}.json"


def test_run_record_serialization(tmp_path):
    recorder = RunRecorder(
        date_str="2026-05-01",
        kind="daily",
        trigger="manual",
        db_path=tmp_path / "keypulse.db",
        records_dir=tmp_path / "run_records",
    )
    recorder.started_at_utc = "2026-05-01T00:00:00Z"
    recorder.finished_at_utc = "2026-05-01T00:01:00Z"
    recorder.mark_stage("load_rows", "ok")
    recorder.mark_stage("anchor_load", "failed", reason="weekly_anchor_decode_failed", error_class="UnicodeDecodeError")
    recorder.set_input_count(42)
    recorder.set_output_quality("degraded", degraded_reason="low_event_count")
    recorder.set_artifact_paths(daily_summary_json="/tmp/daily.json", obsidian_md="/tmp/daily.md")
    recorder.set_cost({"in_tokens": 10, "out_tokens": 5, "cost_usd": 0.01})
    recorder.set_core_watcher_emit_counts({"keyboard_chunk": 3, "clipboard": 2})

    payload = recorder.to_payload()

    assert payload["run_id"]
    assert payload["date"] == "2026-05-01"
    assert payload["kind"] == "daily"
    assert payload["trigger"] == "manual"
    assert payload["input_count"] == 42
    assert payload["stage_status"]["anchor_load"] == "failed"
    assert payload["stage_details"]["anchor_load"]["reason"] == "weekly_anchor_decode_failed"
    assert payload["stage_details"]["anchor_load"]["error_class"] == "UnicodeDecodeError"
    assert payload["artifact_paths"]["daily_summary_json"] == "/tmp/daily.json"
    assert payload["core_watcher_emit_counts"] == {"keyboard_chunk": 3, "clipboard": 2}


def test_run_record_writes_on_success(tmp_path, monkeypatch):
    _patch_home_and_sink(monkeypatch, tmp_path)
    _write_config(tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _insert_event("keyboard_chunk", "2026-05-01T01:00:00+00:00", "typed daily implementation")
    _insert_event("clipboard", "2026-05-01T01:05:00+00:00", "copied run record notes")

    summary = run_daily("2026-05-01", trigger="18:00")

    payload = json.loads(_record_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["date"] == "2026-05-01"
    assert payload["kind"] == "daily"
    assert payload["stage_status"]["load_rows"] == "ok"
    assert payload["stage_status"]["persist"] == "ok"
    assert payload["output_quality"] == "degraded"
    assert payload["degraded_reason"] == "low_event_count"
    assert payload["artifact_paths"]["daily_summary_json"] == summary.summary_path
    assert payload["artifact_paths"]["obsidian_md"] == summary.daily_path

    conn = sqlite3.connect(tmp_path / ".keypulse" / "keypulse.db")
    row = conn.execute("SELECT payload_json FROM run_records WHERE date=? AND kind=?", ("2026-05-01", "daily")).fetchone()
    conn.close()
    assert row is not None
    assert json.loads(row[0])["run_id"] == payload["run_id"]
    close()


def test_run_record_writes_on_failure_with_traceback(tmp_path, monkeypatch):
    _patch_home_and_sink(monkeypatch, tmp_path)
    _write_config(tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _insert_event("keyboard_chunk", "2026-05-14T01:00:00+00:00", "event one")
    _insert_event("clipboard", "2026-05-14T01:05:00+00:00", "event two")
    _insert_event("window", "2026-05-14T01:10:00+00:00", "event three")
    monkeypatch.setenv("MOCK_LLM", "1")

    def fail_anchor_load(_week_str: str):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator.load_weekly_anchors", fail_anchor_load)

    with pytest.raises(UnicodeDecodeError):
        run_daily("2026-05-14", trigger="18:00")

    payload = json.loads(_record_path(tmp_path, "2026-05-14").read_text(encoding="utf-8"))
    assert payload["stage_status"]["anchor_load"] == "failed"
    assert payload["failed_stage"] == "anchor_load"
    assert payload["failure_reason"] == "weekly_anchor_decode_failed"
    assert payload["error_class"] == "UnicodeDecodeError"
    assert "UnicodeDecodeError" in payload["error_trace"]
    close()


def test_core_watcher_emit_counts_correct(tmp_path, monkeypatch):
    _patch_home_and_sink(monkeypatch, tmp_path)
    _write_config(tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _insert_event("keyboard_chunk", "2026-05-01T01:00:00+00:00", "typed one")
    _insert_event("keyboard_chunk", "2026-05-01T02:00:00+00:00", "typed two")
    _insert_event("clipboard", "2026-05-01T03:00:00+00:00", "clip one")
    _insert_event("window", "2026-05-01T04:00:00+00:00", "window one")
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator._filter_for_trigger",
        lambda _date, _trigger, loaded: loaded[:2],
    )

    run_daily("2026-05-01", trigger="18:00")

    payload = json.loads(_record_path(tmp_path).read_text(encoding="utf-8"))
    assert payload["core_watcher_emit_counts"]["keyboard_chunk"] == 2
    assert payload["core_watcher_emit_counts"]["clipboard"] == 1
    assert payload["core_watcher_emit_counts"]["window"] == 1
    close()
