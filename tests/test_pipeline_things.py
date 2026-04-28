from __future__ import annotations

from datetime import datetime, timezone

from click.testing import CliRunner

from keypulse.cli import main
from keypulse.pipeline.entity_extractor import Entity
from keypulse.pipeline.outline_prompt import ThingOutline
from keypulse.pipeline.session_splitter import ActivitySession
from keypulse.pipeline.thing_clusterer import Thing
from keypulse.pipeline.things import build_things, render_things_report
from keypulse.sources.types import SemanticEvent


def _event(ts: str, source: str, intent: str, artifact: str, metadata: dict | None = None) -> SemanticEvent:
    return SemanticEvent(
        time=datetime.fromisoformat(ts),
        source=source,
        actor="Harland",
        intent=intent,
        artifact=artifact,
        raw_ref="",
        privacy_tier="green",
        metadata=metadata or {},
    )


def _thing() -> Thing:
    event = _event("2026-04-28T10:00:00+00:00", "git_log", "实现 timeline", "commit:11a3a9b")
    return Thing(
        id="t1",
        title="修复 timeline",
        entities=[Entity(kind="commit", value="11a3a9b", raw="11a3a9b", confidence=1.0)],
        events=[event],
        time_start=event.time,
        time_end=event.time,
        sources={event.source},
    )


def test_build_things_empty_events(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.pipeline.things.read_all", lambda since, until, source=None: iter(()))
    things = build_things(datetime(2026, 4, 27, tzinfo=timezone.utc), datetime(2026, 4, 28, tzinfo=timezone.utc))
    assert things == []


def test_build_things_with_source_filter(monkeypatch) -> None:
    called: list[str | None] = []

    def fake_read_all(since, until, source=None):
        called.append(source)
        return iter([
            _event("2026-04-28T10:00:00+00:00", "git_log", "实现 timeline", "commit:11a3a9b"),
            _event("2026-04-28T10:10:00+00:00", "claude_code", "修复 timeline", "11a3a9b"),
        ])

    monkeypatch.setattr("keypulse.pipeline.things.read_all", fake_read_all)
    things = build_things(
        datetime(2026, 4, 28, tzinfo=timezone.utc),
        datetime(2026, 4, 29, tzinfo=timezone.utc),
        sources=["git_log", "claude_code"],
        model_gateway=None,
    )

    assert called == ["git_log", "claude_code"]
    assert len(things) == 1
    assert things[0].sources == {"git_log", "claude_code"}
    assert len(things[0].events) == 4


def test_build_things_uses_outline_assign_pipeline(monkeypatch) -> None:
    events = [
        _event("2026-04-28T10:00:00+00:00", "git_log", "修复 timeline", "commit:11a3a9b"),
        _event("2026-04-28T10:05:00+00:00", "claude_code", "补充 timeline 测试", "keypulse/pipeline/things.py"),
    ]
    session = ActivitySession(id="session-1", time_start=events[0].time, time_end=events[-1].time, events=events)

    monkeypatch.setattr("keypulse.pipeline.things.read_all", lambda since, until, source=None: iter(events))
    monkeypatch.setattr("keypulse.pipeline.things.split_into_sessions", lambda events, idle_threshold_minutes=30: [session])
    monkeypatch.setattr(
        "keypulse.pipeline.things.request_outline",
        lambda session, model_gateway=None: [ThingOutline(title="修复 timeline", summary_hint="跨源补丁")],
    )
    monkeypatch.setattr(
        "keypulse.pipeline.things.assign_events",
        lambda events, outlines: {"修复 timeline": events, "_其他": []},
    )

    things = build_things(
        datetime(2026, 4, 28, tzinfo=timezone.utc),
        datetime(2026, 4, 29, tzinfo=timezone.utc),
        model_gateway=None,
    )

    assert len(things) == 1
    assert things[0].title == "修复 timeline"
    assert len(things[0].events) == 2


def test_render_things_report_markdown_fallback() -> None:
    report = render_things_report([_thing()], model_gateway=None, title="今日做的事")
    assert report.startswith("# 今日做的事")
    assert "###" in report
    assert "- 问题：" in report


def test_pipeline_things_cli_json(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.cli.get_config", lambda: type("Cfg", (), {"db_path_expanded": object(), "model": object()})())
    monkeypatch.setattr("keypulse.cli.require_db", lambda cfg: None)
    monkeypatch.setattr("keypulse.cli.load_model_gateway", lambda cfg: object())
    monkeypatch.setattr(
        "keypulse.cli.build_things",
        lambda since, until, model_gateway=None, sources=None, idle_threshold_minutes=30: [],
    )

    result = CliRunner().invoke(main, ["pipeline", "things", "--since", "2026-04-28", "--json", "--no-llm"])
    assert result.exit_code == 0
    assert result.output.strip() == "[]"


def test_pipeline_things_cli_passes_idle_threshold(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.cli.get_config", lambda: type("Cfg", (), {"db_path_expanded": object(), "model": object()})())
    monkeypatch.setattr("keypulse.cli.require_db", lambda cfg: None)
    monkeypatch.setattr("keypulse.cli.load_model_gateway", lambda cfg: object())

    captured: dict[str, int] = {}

    def fake_build_things(since, until, model_gateway=None, sources=None, idle_threshold_minutes=30):
        captured["idle_threshold_minutes"] = idle_threshold_minutes
        return []

    monkeypatch.setattr("keypulse.cli.build_things", fake_build_things)

    result = CliRunner().invoke(main, ["pipeline", "things", "--since", "2026-04-28", "--no-llm", "--idle-threshold", "45"])

    assert result.exit_code == 0
    assert captured["idle_threshold_minutes"] == 45
