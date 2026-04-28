from __future__ import annotations

from datetime import datetime, timezone

from click.testing import CliRunner

from keypulse.cli import main
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
    )
    assert called == ["git_log", "claude_code"]
    assert len(things) == 1
    assert things[0].sources == {"git_log", "claude_code"}


def test_render_things_report_markdown_fallback() -> None:
    thing_event = _event("2026-04-28T10:00:00+00:00", "git_log", "实现 timeline", "commit:11a3a9b")
    monkeypatch_things = build_things.__globals__["cluster"]([thing_event])
    report = render_things_report(monkeypatch_things, model_gateway=None, title="今日做的事")
    assert report.startswith("# 今日做的事")
    assert "###" in report
    assert "- 问题：" in report


def test_pipeline_things_cli_json(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.cli.get_config", lambda: type("Cfg", (), {"db_path_expanded": object(), "model": object()})())
    monkeypatch.setattr("keypulse.cli.require_db", lambda cfg: None)
    monkeypatch.setattr("keypulse.cli.load_model_gateway", lambda cfg: object())
    monkeypatch.setattr(
        "keypulse.cli.build_things",
        lambda since, until, model_gateway=None, sources=None: [],
    )

    result = CliRunner().invoke(main, ["pipeline", "things", "--since", "2026-04-28", "--json", "--no-llm"])
    assert result.exit_code == 0
    assert result.output.strip() == "[]"


def test_pipeline_things_cli_markdown(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.cli.get_config", lambda: type("Cfg", (), {"db_path_expanded": object(), "model": object()})())
    monkeypatch.setattr("keypulse.cli.require_db", lambda cfg: None)
    monkeypatch.setattr("keypulse.cli.load_model_gateway", lambda cfg: object())

    sample = build_things.__globals__["cluster"]([
        _event("2026-04-28T10:00:00+00:00", "git_log", "实现 timeline", "commit:11a3a9b"),
    ])
    monkeypatch.setattr("keypulse.cli.build_things", lambda since, until, model_gateway=None, sources=None: sample)

    result = CliRunner().invoke(main, ["pipeline", "things", "--since", "2026-04-28", "--no-llm"])
    assert result.exit_code == 0
    assert "# 今日做的事" in result.output
