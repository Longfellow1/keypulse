from __future__ import annotations

import json
from dataclasses import dataclass

from click.testing import CliRunner

from keypulse.cli import main


@dataclass
class _ModelCfg:
    active_profile: str = "local-first"
    state_path: str = ""


@dataclass
class _Cfg:
    db_path_expanded: object
    model: _ModelCfg


def test_pipeline_sync_uses_unified_export_path(monkeypatch):
    captured = {}

    def fake_get_config():
        return type(
            "Cfg",
            (),
            {
                "db_path_expanded": object(),
                "obsidian": type("ObsidianCfg", (), {"vault_name": "KeyPulse"})(),
            },
        )()

    def fake_require_db(cfg):
        captured["db_checked"] = True

    def fake_resolve_active_sink(*args, **kwargs):
        captured["sink_resolved"] = True
        return type("Sink", (), {"kind": "obsidian", "output_dir": "/tmp/test-vault", "source": "filesystem"})()

    def fake_export_obsidian(
        output_dir,
        date_str=None,
        vault_name="KeyPulse",
        model_gateway=None,
        incremental=False,
        db_path=None,
        cursor_path=None,
    ):
        captured["output_dir"] = output_dir
        captured["date_str"] = date_str
        captured["vault_name"] = vault_name
        captured["incremental"] = incremental
        captured["db_path"] = db_path
        return ["/tmp/test-vault/Daily/x.md"]

    monkeypatch.setattr("keypulse.cli.get_config", fake_get_config)
    monkeypatch.setattr("keypulse.cli.require_db", fake_require_db)
    monkeypatch.setattr("keypulse.cli.resolve_active_sink", fake_resolve_active_sink)
    monkeypatch.setattr("keypulse.cli.export_obsidian", fake_export_obsidian)

    result = CliRunner().invoke(main, ["pipeline", "sync", "--date", "2026-04-18"])

    assert result.exit_code == 0
    assert "pipeline_sync=ok" in result.output
    assert captured["db_checked"] is True
    assert captured["sink_resolved"] is True
    assert captured["output_dir"] == "/tmp/test-vault"
    assert captured["date_str"] == "2026-04-18"
    assert captured["vault_name"] == "KeyPulse"
    assert captured["incremental"] is False
    assert captured["db_path"] is not None


def test_search_command_uses_resolver_backend(monkeypatch):
    captured = {}

    class FakeBackend:
        kind = "fts"

        def search(self, query, app_name=None, since=None, source=None, limit=50):
            captured["query"] = query
            captured["app_name"] = app_name
            captured["since"] = since
            captured["source"] = source
            captured["limit"] = limit
            return [{"title": "match", "created_at": "2026-04-18T00:00:00+00:00", "ref_type": "manual", "app_name": "Terminal", "body": "text"}]

    monkeypatch.setattr("keypulse.cli.get_config", lambda: type("Cfg", (), {"db_path_expanded": object()})())
    monkeypatch.setattr("keypulse.cli.require_db", lambda cfg: None)
    monkeypatch.setattr("keypulse.cli.resolve_search_backend", lambda: FakeBackend())

    result = CliRunner().invoke(main, ["search", "query", "--limit", "3"])

    assert result.exit_code == 0
    assert captured["query"] == "query"
    assert captured["limit"] == 3


def test_model_use_persists_profile(monkeypatch, tmp_path):
    state_path = tmp_path / "model-state.json"

    monkeypatch.setattr(
        "keypulse.cli.get_config",
        lambda: type(
            "Cfg",
            (),
            {
                "db_path_expanded": object(),
                "model": _ModelCfg(active_profile="local-first", state_path=str(state_path)),
            },
        )(),
    )

    result = CliRunner().invoke(main, ["model", "use", "privacy-locked"])

    assert result.exit_code == 0
    payload = json.loads(state_path.read_text())
    assert payload["active_profile"] == "privacy-locked"
