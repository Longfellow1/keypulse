from __future__ import annotations

from click.testing import CliRunner

from keypulse.cli import main


def test_sinks_detect_reports_binding(monkeypatch):
    monkeypatch.setattr(
        "keypulse.cli.resolve_active_sink",
        lambda *args, **kwargs: type(
            "Sink",
            (),
            {
                "kind": "obsidian",
                "output_dir": "/tmp/Knowledge",
                "source": "filesystem",
            },
        )(),
    )

    result = CliRunner().invoke(main, ["sinks", "detect"])

    assert result.exit_code == 0
    assert "obsidian" in result.output
