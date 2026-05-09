from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from keypulse.pipeline.daily_orchestrator import run_daily
from keypulse.pipeline.daily_summary import write_daily_summary


class FakeGateway:
    def __init__(self, response: dict[str, Any]):
        self.response = response
        self.calls: list[str] = []

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs) -> Any:
        self.calls.append(capability)
        return self.response


def _patch_io(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_rows_for_date", lambda _date: rows)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._filter_for_trigger", lambda _date, _trigger, loaded: loaded)
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "source": "ax_text",
            "speaker": "user",
            "ts_start": "2026-05-03T01:00:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "content_text": "one",
            "metadata_json": json.dumps({"entities": {"session_id": "s1"}}),
        },
        {
            "id": 2,
            "source": "ax_text",
            "speaker": "ai",
            "ts_start": "2026-05-03T01:03:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "content_text": "two",
            "metadata_json": json.dumps({"entities": {"session_id": "s1"}}),
        },
        {
            "id": 3,
            "source": "clipboard",
            "speaker": "user",
            "ts_start": "2026-05-03T03:00:00+00:00",
            "app_name": "Chrome",
            "window_title": "Misc",
            "content_text": "three",
            "metadata_json": json.dumps({"entities": {"session_id": "s2"}}),
        },
    ]


def _seed_daily_summary(date_str: str) -> None:
    write_daily_summary(
        date_str,
        clusters=[],
        misc=[],
        topic_snapshot={},
        cost={"in_tokens": 1, "out_tokens": 1, "cost_usd": 0.0},
    )


class FridayAfterFive(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 5, 8, 17, 30, tzinfo=tz)


class FridayBeforeFive(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 5, 8, 9, 0, tzinfo=tz)


def test_friday_after_5pm_triggers_weekly_once_when_week_has_enough_dailies(tmp_path, monkeypatch):
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway({"markdown": "# ok"})
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._resolve_daily_tier", lambda _gateway: "flagship")
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator.datetime", FridayAfterFive)

    _seed_daily_summary("2026-05-04")
    _seed_daily_summary("2026-05-05")
    _seed_daily_summary("2026-05-06")

    called: list[str] = []

    def fake_run_weekly(week_str: str) -> str:
        called.append(week_str)
        return ""

    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator.run_weekly", fake_run_weekly)

    run_daily("2026-05-08", trigger="18:00")

    assert called == ["2026-W19"]


def test_friday_before_5pm_does_not_trigger_weekly(tmp_path, monkeypatch):
    rows = list(_rows())
    for row in rows:
        row["ts_start"] = str(row["ts_start"]).replace("2026-05-03", "2026-05-01")
    _patch_io(monkeypatch, tmp_path, rows)
    gateway = FakeGateway({"markdown": "# ok"})
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._resolve_daily_tier", lambda _gateway: "flagship")
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator.datetime", FridayBeforeFive)

    called: list[str] = []

    def fake_run_weekly(week_str: str) -> str:
        called.append(week_str)
        return ""

    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator.run_weekly", fake_run_weekly)

    run_daily("2026-05-01", trigger="18:00")

    assert called == []


def test_sunday_does_not_trigger_weekly_even_after_5pm(tmp_path, monkeypatch):
    rows = list(_rows())
    for row in rows:
        row["ts_start"] = str(row["ts_start"]).replace("2026-05-03", "2026-05-10")
    _patch_io(monkeypatch, tmp_path, rows)
    gateway = FakeGateway({"markdown": "# ok"})
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._resolve_daily_tier", lambda _gateway: "flagship")
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator.datetime", FridayAfterFive)

    called: list[str] = []

    def fake_run_weekly(week_str: str) -> str:
        called.append(week_str)
        return ""

    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator.run_weekly", fake_run_weekly)

    run_daily("2026-05-10", trigger="18:00")

    assert called == []


def test_monday_does_not_trigger_weekly(tmp_path, monkeypatch):
    rows = list(_rows())
    for row in rows:
        row["ts_start"] = str(row["ts_start"]).replace("2026-05-03", "2026-05-11")
    _patch_io(monkeypatch, tmp_path, rows)
    gateway = FakeGateway({"markdown": "# ok"})
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._resolve_daily_tier", lambda _gateway: "flagship")
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator.datetime", FridayAfterFive)

    called: list[str] = []

    def fake_run_weekly(week_str: str) -> str:
        called.append(week_str)
        return ""

    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator.run_weekly", fake_run_weekly)

    run_daily("2026-05-11", trigger="18:00")

    assert called == []


def test_weekly_exception_is_swallowed_and_daily_still_succeeds(tmp_path, monkeypatch):
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway({"markdown": "# ok"})
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._resolve_daily_tier", lambda _gateway: "flagship")
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator.datetime", FridayAfterFive)

    def boom(_week_str: str) -> str:
        raise RuntimeError("weekly blew up")

    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator.run_weekly", boom)

    summary = run_daily("2026-05-08", trigger="18:00")

    assert summary.skipped is False
