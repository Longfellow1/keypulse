from __future__ import annotations

from click.testing import CliRunner

from keypulse.cli import main


def test_serve_invokes_foreground_runner(monkeypatch):
    captured = {}

    def fake_load():
        return type("Cfg", (), {"db_path_expanded": object(), "log_path_expanded": object()})()

    def fake_run(cfg):
        captured["cfg"] = cfg

    monkeypatch.setattr("keypulse.cli.Config.load", fake_load)
    monkeypatch.setattr("keypulse.cli.run", fake_run)

    result = CliRunner().invoke(main, ["serve"])

    assert result.exit_code == 0
    assert "cfg" in captured
