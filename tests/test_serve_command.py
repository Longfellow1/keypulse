from __future__ import annotations

from click.testing import CliRunner

from keypulse.cli import _model_backends_need_setup, main
from keypulse.config import Config


def test_serve_invokes_foreground_runner(monkeypatch):
    captured = {}

    def fake_load():
        return Config.model_validate(
            {
                "model": {
                    "local": {"kind": "disabled"},
                    "cloud": {"kind": "disabled"},
                }
            }
        )

    def fake_run(cfg):
        captured["cfg"] = cfg

    monkeypatch.setattr("keypulse.cli.Config.load", fake_load)
    monkeypatch.setattr("keypulse.cli.run", fake_run)

    result = CliRunner().invoke(main, ["serve"])

    assert result.exit_code == 0
    assert "cfg" in captured


def test_model_backends_need_setup_when_local_incomplete_and_cloud_key_missing(monkeypatch):
    cfg = Config.model_validate(
        {
            "model": {
                "local": {
                    "kind": "lm_studio",
                    "base_url": "",
                    "model": "local-model",
                },
                "cloud": {
                    "kind": "openai_compatible",
                    "base_url": "https://api.example.com/v1",
                    "model": "cloud-model",
                    "api_key_source": "keychain:com.keypulse.missing",
                    "api_key_env": "KEYPULSE_MISSING_API_KEY",
                },
            }
        }
    )

    monkeypatch.delenv("KEYPULSE_MISSING_API_KEY", raising=False)
    monkeypatch.setattr("keypulse.cli.read_secret", lambda service: None)

    assert _model_backends_need_setup(cfg) is True
