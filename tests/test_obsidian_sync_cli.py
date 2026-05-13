from __future__ import annotations

from dataclasses import dataclass
from click.testing import CliRunner

from keypulse.cli import main
from keypulse.utils.dates import resolve_local_date


@dataclass
class _ObsidianConfig:
    vault_path: str = "/tmp/test-vault"
    vault_name: str = "KeyPulse"
    wiki_link_mode: str = "relative"
    humanize_titles: bool = False


@dataclass
class _Config:
    db_path_expanded: object
    obsidian: _ObsidianConfig


def test_obsidian_sync_defaults_to_today(monkeypatch):
    captured = {}

    def fake_get_config():
        return type("Cfg", (), {
            "db_path_expanded": object(),
            "obsidian": _ObsidianConfig(),
        })()

    def fake_require_db(cfg):
        captured["db_checked"] = True

    def fake_resolve_active_sink(*args, **kwargs):
        captured["sink_resolved"] = True
        return type("Sink", (), {"kind": "obsidian", "output_dir": "/tmp/test-vault", "source": "filesystem"})()

    def fake_export_obsidian(
        output_dir,
        days=None,
        date_str=None,
        vault_name="KeyPulse",
        model_gateway=None,
        incremental=False,
        db_path=None,
        cursor_path=None,
        wiki_link_mode="relative",
        humanize_titles=False,
    ):
        captured["output_dir"] = output_dir
        captured["days"] = days
        captured["date_str"] = date_str
        captured["vault_name"] = vault_name
        captured["incremental"] = incremental
        captured["db_path"] = db_path
        return ["/tmp/test-vault/Daily/x.md"]

    def fake_should_trigger(kind, **kwargs):
        # Always allow for testing (bypass T1 gate)
        return True, ""

    def fake_record_trigger(*args, **kwargs):
        # No-op for testing
        pass

    monkeypatch.setattr("keypulse.cli.get_config", fake_get_config)
    monkeypatch.setattr("keypulse.cli.require_db", fake_require_db)
    monkeypatch.setattr("keypulse.cli.resolve_active_sink", fake_resolve_active_sink)
    monkeypatch.setattr("keypulse.cli.export_obsidian", fake_export_obsidian)
    monkeypatch.setattr("keypulse.cli.should_trigger", fake_should_trigger)
    monkeypatch.setattr("keypulse.cli.record_trigger", fake_record_trigger)

    runner = CliRunner()
    result = runner.invoke(main, ["obsidian", "sync"])

    assert result.exit_code == 0
    expected_today = resolve_local_date("today", yesterday=False)
    assert captured["db_checked"] is True
    assert captured["sink_resolved"] is True
    assert captured["output_dir"] == "/tmp/test-vault"
    assert captured["date_str"] == expected_today
    assert captured["vault_name"] == "KeyPulse"
    assert captured["incremental"] is False
    assert captured["db_path"] is not None


def test_obsidian_sync_incremental_defaults_to_today(monkeypatch):
    captured = {}

    def fake_get_config():
        return type("Cfg", (), {
            "db_path_expanded": object(),
            "obsidian": _ObsidianConfig(),
        })()

    def fake_require_db(cfg):
        captured["db_checked"] = True

    def fake_resolve_active_sink(*args, **kwargs):
        captured["sink_resolved"] = True
        return type("Sink", (), {"kind": "obsidian", "output_dir": "/tmp/test-vault", "source": "filesystem"})()

    def fake_export_obsidian(
        output_dir,
        days=None,
        date_str=None,
        vault_name="KeyPulse",
        model_gateway=None,
        incremental=False,
        db_path=None,
        cursor_path=None,
        wiki_link_mode="relative",
        humanize_titles=False,
    ):
        captured["output_dir"] = output_dir
        captured["days"] = days
        captured["date_str"] = date_str
        captured["vault_name"] = vault_name
        captured["incremental"] = incremental
        captured["db_path"] = db_path
        return ["/tmp/test-vault/Daily/x.md"]

    def fake_should_trigger(kind, **kwargs):
        # Always allow for testing (bypass T1 gate)
        return True, ""

    def fake_record_trigger(*args, **kwargs):
        # No-op for testing
        pass

    monkeypatch.setattr("keypulse.cli.get_config", fake_get_config)
    monkeypatch.setattr("keypulse.cli.require_db", fake_require_db)
    monkeypatch.setattr("keypulse.cli.resolve_active_sink", fake_resolve_active_sink)
    monkeypatch.setattr("keypulse.cli.export_obsidian", fake_export_obsidian)
    monkeypatch.setattr("keypulse.cli.should_trigger", fake_should_trigger)
    monkeypatch.setattr("keypulse.cli.record_trigger", fake_record_trigger)

    runner = CliRunner()
    result = runner.invoke(main, ["obsidian", "sync", "--incremental"])

    assert result.exit_code == 0
    expected_today = resolve_local_date("today", yesterday=False)
    assert captured["db_checked"] is True
    assert captured["sink_resolved"] is True
    assert captured["output_dir"] == "/tmp/test-vault"
    assert captured["date_str"] == expected_today
    assert captured["vault_name"] == "KeyPulse"
    assert captured["incremental"] is True
    assert captured["db_path"] is not None


def test_sync_bundle_runs_daily_orchestrator_after_event_export(monkeypatch, tmp_path):
    from keypulse.cli import _sync_obsidian_bundle

    called: list[tuple[str, object]] = []

    cfg = type("Cfg", (), {
        "db_path_expanded": tmp_path / "keypulse.db",
        "obsidian": _ObsidianConfig(),
    })()

    monkeypatch.setattr(
        "keypulse.cli.resolve_active_sink",
        lambda *args, **kwargs: type("Sink", (), {"kind": "obsidian", "output_dir": tmp_path / "vault", "source": "filesystem"})(),
    )
    monkeypatch.setattr("keypulse.cli.load_model_gateway", lambda cfg: object())
    monkeypatch.setattr("keypulse.cli.export_obsidian", lambda *args, **kwargs: [tmp_path / "event.md"])
    monkeypatch.setattr(
        "keypulse.cli.run_daily_after_obsidian_sync",
        lambda date_str, *, db_path: called.append((date_str, db_path)),
    )

    written, _target, _kind = _sync_obsidian_bundle(cfg, "2026-05-13", incremental=True)

    assert written == 1
    assert called == [("2026-05-13", tmp_path / "keypulse.db")]


def test_obsidian_sync_rejects_mutually_exclusive_options(monkeypatch):
    def fake_get_config():
        return type("Cfg", (), {"db_path_expanded": object(), "obsidian": _ObsidianConfig()})()

    monkeypatch.setattr("keypulse.cli.get_config", fake_get_config)
    monkeypatch.setattr("keypulse.cli.require_db", lambda cfg: None)

    runner = CliRunner()
    result = runner.invoke(main, ["obsidian", "sync", "--incremental", "--yesterday"])

    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
