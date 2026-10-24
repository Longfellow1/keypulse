from __future__ import annotations

from click.testing import CliRunner

from penpal.cli import main
from penpal.config import Config


def test_serve_invokes_foreground_runner(monkeypatch):
    captured = {}

    def fake_load():
        return Config()

    def fake_run(cfg):
        captured["cfg"] = cfg

    monkeypatch.setattr("penpal.cli.daemon.Config.load", fake_load)
    monkeypatch.setattr("penpal.cli.daemon.run", fake_run)

    result = CliRunner().invoke(main, ["serve"])

    assert result.exit_code == 0
    assert "cfg" in captured


def test_mcp_server_identity_is_penpal():
    from penpal.mcp.server import build_server

    assert build_server().name == "penpal"
