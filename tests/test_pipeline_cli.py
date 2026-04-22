from __future__ import annotations

from click.testing import CliRunner

from keypulse.cli import main


def test_pipeline_draft_prints_daily_body(monkeypatch):
    def fake_get_config():
        return type(
            "Cfg",
            (),
            {
                "db_path_expanded": object(),
                "pipeline": type("PipelineCfg", (), {"feedback_path": "/tmp/feedback.jsonl"})(),
            },
        )()

    monkeypatch.setattr("keypulse.cli.get_config", fake_get_config)
    monkeypatch.setattr("keypulse.cli.require_db", lambda cfg: None)
    monkeypatch.setattr(
        "keypulse.cli.query_raw_events",
        lambda since=None, until=None, limit=50000: [
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "title": "Fix install path",
                "body": "install.sh now reuses the venv",
            }
        ],
    )

    result = CliRunner().invoke(main, ["pipeline", "draft", "--date", "2026-04-18"])

    assert result.exit_code == 0
    assert "碎片汇总" in result.output
    assert "另有 1 个零散片段" in result.output


def test_pipeline_feedback_round_trip(monkeypatch, tmp_path):
    feedback_path = tmp_path / "feedback.jsonl"

    def fake_get_config():
        return type(
            "Cfg",
            (),
            {
                "db_path_expanded": object(),
                "pipeline": type("PipelineCfg", (), {"feedback_path": str(feedback_path)})(),
            },
        )()

    monkeypatch.setattr("keypulse.cli.get_config", fake_get_config)
    monkeypatch.setattr("keypulse.cli.require_db", lambda cfg: None)

    runner = CliRunner()
    add_result = runner.invoke(
        main,
        ["pipeline", "feedback", "add", "--kind", "promote", "--target", "decision-making", "--note", "repeat topic"],
    )
    list_result = runner.invoke(main, ["pipeline", "feedback", "list", "--plain"])

    assert add_result.exit_code == 0
    assert list_result.exit_code == 0
    assert "promote" in list_result.output
    assert "decision-making" in list_result.output
