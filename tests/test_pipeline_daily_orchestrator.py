from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from keypulse.cli import main
from keypulse.pipeline.daily_orchestrator import run_daily
from keypulse.store.db import close, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


def _insert_event(
    *,
    ts_start: str,
    content: str,
    session_id: str,
    app: str = "Codex",
    window: str = "KeyPulse",
    entities: dict | None = None,
) -> None:
    payload = {"entities": dict(entities or {})}
    payload["entities"]["session_id"] = session_id
    event = RawEvent(
        source="window",
        event_type="window_title_changed",
        ts_start=ts_start,
        app_name=app,
        window_title=window,
        content_text=content,
        metadata_json=json.dumps(payload, ensure_ascii=False),
        session_id=session_id,
    )
    insert_raw_event(event)


def _seed_minimal_events() -> None:
    _insert_event(
        ts_start="2026-05-01T01:00:00+00:00",
        content="实现 daily orchestrator 主流程",
        session_id="s1",
        entities={"file_paths": ["keypulse/pipeline/daily_orchestrator.py"], "named_entities": ["keypulse"]},
    )
    _insert_event(
        ts_start="2026-05-01T01:03:00+00:00",
        content="补 L1 schema 并校验输出",
        session_id="s1",
        entities={"file_paths": ["keypulse/prompts/schemas/L1_output.json"], "named_entities": ["schema"]},
    )
    _insert_event(
        ts_start="2026-05-01T01:20:00+00:00",
        content="补 L2 narrative prompt",
        session_id="s2",
        entities={"file_paths": ["keypulse/prompts/L2_narrative.v1.md"], "named_entities": ["narrative"]},
    )
    _insert_event(
        ts_start="2026-05-01T01:24:00+00:00",
        content="更新 topic naming 规则",
        session_id="s2",
        entities={"file_paths": ["keypulse/prompts/L3_topic_naming.v1.md"], "named_entities": ["topic"]},
    )
    _insert_event(
        ts_start="2026-05-01T02:10:00+00:00",
        content="misc random browsing",
        session_id="s3",
        entities={"urls": ["https://example.com/random?x=1"]},
    )


def test_daily_orchestrator_stub_gateway_full_chain(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_minimal_events()
    monkeypatch.setenv("MOCK_LLM", "1")

    summary = run_daily("2026-05-01", trigger="18:00")

    daily_path = Path(summary.daily_path)
    assert daily_path.exists()
    body = daily_path.read_text(encoding="utf-8")
    assert "## 今日主线" in body
    assert "## 散点" in body

    summary_path = Path(summary.summary_path)
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["date"] == "2026-05-01"
    assert "clusters" in payload

    assert (tmp_path / ".keypulse" / "hot.md").exists()
    assert (tmp_path / ".keypulse" / "log.md").exists()
    assert list((tmp_path / ".keypulse" / "topics").glob("*.md"))
    assert summary.topic_diffs
    close()


def test_daily_orchestrator_cache_key_distinguishes_trigger(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_minimal_events()
    monkeypatch.setenv("MOCK_LLM", "1")

    run_daily("2026-05-01", trigger="18:00")
    state_path = tmp_path / ".keypulse" / "daily-orchestrator-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["dates"]["2026-05-01"]["last_1800_event_id"] = 0
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    run_daily("2026-05-01", trigger="23:30")

    cache_dir = tmp_path / ".keypulse" / "cache" / "llm"
    triggers = set()
    for cache_file in cache_dir.glob("*.json"):
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        trigger = (
            payload.get("input", {})
            .get("input_data", {})
            .get("trigger")
        )
        if trigger:
            triggers.add(trigger)
    assert {"18:00", "23:30"} <= triggers
    close()


def test_daily_cli_falls_back_to_things_when_mock_llm_keeps_failing(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_minimal_events()
    monkeypatch.setenv("MOCK_LLM_FAILS", "99")
    fallback_target = tmp_path / "vault" / "Daily" / "2026-05-01.md"

    def fake_fallback(_cfg, _date_str, *, no_llm):
        fallback_target.parent.mkdir(parents=True, exist_ok=True)
        fallback_target.write_text("# fallback\n\n## 今日概览\n\n- things fallback\n", encoding="utf-8")
        return fallback_target

    monkeypatch.setattr("keypulse.cli._render_daily_fallback_with_things", fake_fallback)

    result = CliRunner().invoke(
        main,
        [
            "daily",
            "run",
            "--date",
            "2026-05-01",
            "--trigger",
            "18:00",
            "--mock-llm",
        ],
    )

    assert result.exit_code == 0
    assert "daily_run=fallback" in result.output
    assert "fallback_daily_path=" in result.output
    assert fallback_target.exists()
    close()
