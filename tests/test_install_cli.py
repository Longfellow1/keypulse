from __future__ import annotations

import plistlib
from pathlib import Path

from click.testing import CliRunner

from keypulse.cli import main


def test_install_help_exposes_lifecycle_commands():
    result = CliRunner().invoke(main, ["install", "--help"])

    assert result.exit_code == 0
    assert "init" in result.output
    assert "launchd" in result.output
    assert "doctor" in result.output
    assert "uninstall" in result.output


def test_install_launchd_writes_plists_with_absolute_keypulse_path(monkeypatch, tmp_path):
    executable = tmp_path / "bin" / "keypulse"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("keypulse.cli._keypulse_executable_path", lambda: executable)

    result = CliRunner().invoke(main, ["install", "launchd", "--no-load"])

    assert result.exit_code == 0
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    daemon_plist = launch_agents / "com.keypulse.daemon.plist"
    health_plist = launch_agents / "com.keypulse.healthcheck.plist"
    daily_plist = launch_agents / "com.keypulse.obsidian-sync.plist"
    hourly_plist = launch_agents / "com.keypulse.obsidian-sync-hourly.plist"
    for path in (daemon_plist, health_plist, daily_plist, hourly_plist):
        assert path.exists()

    payload = plistlib.loads(daemon_plist.read_bytes())
    assert payload["ProgramArguments"] == [str(executable), "serve"]
    assert payload["StandardOutPath"] == str(tmp_path / ".keypulse" / "logs" / "daemon.log")

    hourly = plistlib.loads(hourly_plist.read_bytes())
    assert hourly["ProgramArguments"] == [str(executable), "obsidian", "sync", "--incremental"]


def test_install_launchd_preserves_custom_plist_without_force(monkeypatch, tmp_path):
    executable = tmp_path / "bin" / "keypulse"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    custom = launch_agents / "com.keypulse.daemon.plist"
    custom.write_text("custom plist", encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("keypulse.cli._keypulse_executable_path", lambda: executable)

    result = CliRunner().invoke(main, ["install", "launchd", "--no-load"])

    assert result.exit_code != 0
    assert "already exists and differs" in result.output
    assert custom.read_text(encoding="utf-8") == "custom plist"


def test_install_init_creates_runtime_without_overwriting_config(monkeypatch, tmp_path):
    executable = tmp_path / "bin" / "keypulse"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    config_path = tmp_path / ".keypulse" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[app]\nretention_days = 99\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("keypulse.cli._keypulse_executable_path", lambda: executable)
    monkeypatch.setattr("keypulse.cli.resolve_active_sink", lambda cfg, persist=False: None)

    result = CliRunner().invoke(main, ["install", "init", "--no-launchd"])

    assert result.exit_code == 0
    assert (tmp_path / ".keypulse" / "logs").exists()
    assert (tmp_path / ".keypulse" / "keypulse.db").exists()
    assert config_path.read_text(encoding="utf-8") == "[app]\nretention_days = 99\n"
    assert "keypulse setup" in result.output
