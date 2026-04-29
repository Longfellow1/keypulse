from __future__ import annotations

from datetime import datetime, timezone

from click.testing import CliRunner

from keypulse.cli import main
from keypulse.pipeline.entity_extractor import Entity
from keypulse.pipeline.model import PipelineQualityError
from keypulse.pipeline.session_splitter import ActivitySession
from keypulse.pipeline.thing import Thing
from keypulse.pipeline.things import build_things, render_things_report
from keypulse.sources.types import SemanticEvent


class _Gateway:
    def render(self, prompt: str) -> str:
        if "请识别这段时间用户做的" in prompt or "事件流（按时间排序" in prompt:
            return (
                "### 修复 timeline\n"
                "你在 Terminal 和 Git 里推进了 KeyPulse 相关修改并完成验证，commit 11a3a9b 落地。"
            )
        if "今日做事概览" in prompt:
            return "你今天围绕 KeyPulse 修复 timeline，并保留了 commit 11a3a9b 这条关键事实。"
        return "ok"


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
        narrative="你在 Git 里把 timeline 修了，commit 11a3a9b 留下了关键事实。",
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
        model_gateway=_Gateway(),
    )

    assert called == ["git_log", "claude_code"]
    assert len(things) == 1
    assert things[0].sources == {"git_log", "claude_code"}
    assert len(things[0].events) == 4
    assert things[0].narrative
    assert "11a3a9b" in things[0].narrative


def test_build_things_uses_session_renderer(monkeypatch) -> None:
    events = [
        _event("2026-04-28T10:00:00+00:00", "git_log", "修复 timeline", "commit:11a3a9b"),
        _event("2026-04-28T10:05:00+00:00", "claude_code", "补充 timeline 测试", "keypulse/pipeline/things.py"),
    ]
    session = ActivitySession(id="session-1", time_start=events[0].time, time_end=events[-1].time, events=events)

    monkeypatch.setattr("keypulse.pipeline.things.read_all", lambda since, until, source=None: iter(events))
    monkeypatch.setattr("keypulse.pipeline.things.split_into_sessions", lambda events, idle_threshold_minutes=30: [session])

    captured: dict[str, object] = {}

    def fake_render(session_arg, *, model_gateway):
        captured["session_id"] = session_arg.id
        captured["events_count"] = len(session_arg.events)
        return [
            Thing(
                id="t1",
                title="修复 timeline",
                entities=[],
                events=list(session_arg.events),
                time_start=session_arg.time_start,
                time_end=session_arg.time_end,
                sources={event.source for event in session_arg.events},
                narrative="一段叙事",
            )
        ]

    monkeypatch.setattr("keypulse.pipeline.things.render_session_things", fake_render)

    things = build_things(
        datetime(2026, 4, 28, tzinfo=timezone.utc),
        datetime(2026, 4, 29, tzinfo=timezone.utc),
        model_gateway=_Gateway(),
    )

    assert captured["session_id"] == "session-1"
    assert captured["events_count"] == 2
    assert len(things) == 1
    assert things[0].title == "修复 timeline"
    assert things[0].narrative == "一段叙事"


def test_build_things_session_failure_falls_back(monkeypatch) -> None:
    ok_events = [
        _event("2026-04-28T10:00:00+00:00", "git_log", "提交 timeline", "commit:11a3a9b"),
        _event("2026-04-28T10:05:00+00:00", "claude_code", "补测试", "tests/test_pipeline_things.py"),
    ]
    bad_events = [
        _event("2026-04-28T12:00:00+00:00", "chrome_history", "查看日志", "https://example.com"),
        _event("2026-04-28T12:05:00+00:00", "zsh_history", "排查失败", "pytest"),
    ]
    sessions = [
        ActivitySession(id="ok-session", time_start=ok_events[0].time, time_end=ok_events[-1].time, events=ok_events),
        ActivitySession(id="bad-session", time_start=bad_events[0].time, time_end=bad_events[-1].time, events=bad_events),
    ]

    monkeypatch.setattr("keypulse.pipeline.things.read_all", lambda since, until, source=None: iter(ok_events + bad_events))
    monkeypatch.setattr("keypulse.pipeline.things.split_into_sessions", lambda events, idle_threshold_minutes=30: sessions)

    def fake_render(session_arg, *, model_gateway):
        if session_arg.id == "bad-session":
            raise PipelineQualityError("session_renderer: LLM call failed")
        return [
            Thing(
                id="ok-thing",
                title="推进修复",
                entities=[],
                events=list(session_arg.events),
                time_start=session_arg.time_start,
                time_end=session_arg.time_end,
                sources={event.source for event in session_arg.events},
                narrative="正常输出",
            )
        ]

    monkeypatch.setattr("keypulse.pipeline.things.render_session_things", fake_render)

    things = build_things(
        datetime(2026, 4, 28, tzinfo=timezone.utc),
        datetime(2026, 4, 29, tzinfo=timezone.utc),
        model_gateway=_Gateway(),
    )

    assert len(things) == 2
    fallback = next(thing for thing in things if "生成失败，仅显示骨架" in thing.narrative)
    assert "20:00-20:05 在" in fallback.title
    assert "Chrome、Terminal" in fallback.title
    assert fallback.sources == {"chrome_history", "zsh_history"}


def test_render_things_report_uses_thing_narrative() -> None:
    report = render_things_report([_thing()], model_gateway=_Gateway(), title="今日做的事")
    assert report.startswith("# 今日做的事")
    assert "## 今日概览" in report
    assert "### 修复 timeline" in report
    assert "11a3a9b" in report
    assert "KeyPulse" in report


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
