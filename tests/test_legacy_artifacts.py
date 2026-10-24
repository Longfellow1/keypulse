from __future__ import annotations

import json
import plistlib
from pathlib import Path

from click.testing import CliRunner

from penpal import cli
from penpal.cli.legacy import find_legacy_artifacts
from penpal.cli import maintenance as maintenance_cli
from penpal.runtime.control import LEGACY_LAUNCHD_LABELS
from penpal.utils.paths import MIGRATION_MARKER


def test_finder_reports_every_legacy_artifact_kind_in_injected_tree(tmp_path):
    home = tmp_path / "home"
    apps = home / "Applications"
    apps.mkdir(parents=True)
    named_app = apps / "KeyPulse.app"
    named_app.mkdir()
    bundle_id_app = apps / "Old Launcher.app" / "Contents"
    bundle_id_app.mkdir(parents=True)
    with (bundle_id_app / "Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "com.keypulse.desktop", "CFBundleName": "Old Launcher"}, handle)

    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / f"{LEGACY_LAUNCHD_LABELS[0]}.plist").write_bytes(b"plist")

    data_dir = home / ".penpal"
    (data_dir / "run").mkdir(parents=True)
    (data_dir / "logs").mkdir()
    (data_dir / "keypulse.log").write_text("legacy", encoding="utf-8")
    (data_dir / "run" / "keypulse-daemon.pid").write_text("123", encoding="utf-8")
    (data_dir / "logs" / "keypulse-old.log").write_text("legacy", encoding="utf-8")
    legacy_dir = home / ".keypulse"
    legacy_dir.mkdir()

    artifacts = find_legacy_artifacts(home, [apps], agents, data_dir)
    kinds = [item["kind"] for item in artifacts]

    assert kinds.count("legacy_app") == 2
    assert "legacy_pid" in kinds
    assert kinds.count("legacy_log") == 2
    assert "legacy_launchd_plist" in kinds
    assert "legacy_data_dir" in kinds
    app_item = next(item for item in artifacts if item["kind"] == "legacy_app" and item["path"].endswith("KeyPulse.app"))
    assert app_item["fix_command"].startswith(f"mv {named_app} ~/.Trash/")
    assert all(set(item) == {"kind", "path", "why", "fix_command"} for item in artifacts)
    assert named_app.exists() and (data_dir / "keypulse.log").exists()


def test_doctor_text_and_json_report_legacy_files_as_warning(monkeypatch, tmp_path):
    from penpal.cli import doctor as doctor_cli
    from penpal.runtime import RuntimeState, RuntimeStatus
    from penpal.health import capture_health
    from penpal.sources import onboarding, registry

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(doctor_cli, "evaluate", lambda: RuntimeStatus(RuntimeState.RUNNING))
    monkeypatch.setattr(doctor_cli, "get_config_path", lambda: home / ".penpal" / "config.toml")
    monkeypatch.setattr(doctor_cli, "_stale_moved_dev_venv_script", lambda: None)
    monkeypatch.setattr(doctor_cli, "_legacy_mcp_config_findings", lambda: [])
    monkeypatch.setattr(doctor_cli, "find_legacy_artifacts", lambda **_kwargs: [{
        "kind": "legacy_app",
        "path": "/Applications/KeyPulse.app",
        "why": "old app",
        "fix_command": "mv /Applications/KeyPulse.app ~/.Trash/",
    }])
    monkeypatch.setattr(capture_health, "permission_report", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(onboarding, "source_problems", lambda _names: [])
    monkeypatch.setattr(registry, "list_sources", lambda: [])
    (home / ".penpal").mkdir()
    config_path = home / ".penpal" / "config.toml"
    config_path.write_text("", encoding="utf-8")

    runner = CliRunner()
    text_result = runner.invoke(cli.main, ["doctor", "--plain"])
    assert text_result.exit_code == 0, text_result.output
    assert any(label in text_result.output for label in ("遗留文件", "Legacy files"))
    assert "WARN" in text_result.output
    assert "/Applications/KeyPulse.app" in text_result.output
    assert "mv /Applications/KeyPulse.app ~/.Trash/" in text_result.output

    json_result = runner.invoke(cli.main, ["doctor", "--json"])
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["legacy_artifacts"][0]["kind"] == "legacy_app"
    assert payload["legacy_artifacts"][0]["fix_command"].startswith("mv ")


def test_clean_legacy_removes_only_data_dir_pid_and_logs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    data_dir = home / ".penpal"
    (data_dir / "run").mkdir(parents=True)
    (data_dir / "logs").mkdir()
    owned_files = (
        data_dir / "keypulse.pid",
        data_dir / "run" / "keypulse-hud.pid",
        data_dir / "logs" / "keypulse.log",
    )
    for path in owned_files:
        path.write_text("legacy", encoding="utf-8")
    keep = data_dir / "penpal.log"
    keep.write_text("current", encoding="utf-8")

    apps = home / "Applications"
    app = apps / "KeyPulse.app"
    app.mkdir(parents=True)
    old_plists = home / "Library" / "LaunchAgents"
    old_plists.mkdir(parents=True)
    old_plist = old_plists / f"{LEGACY_LAUNCHD_LABELS[0]}.plist"
    old_plist.write_bytes(b"old plist")

    monkeypatch.setattr(maintenance_cli, "get_data_dir", lambda: data_dir)
    result = CliRunner().invoke(cli.main, ["maintenance", "clean-legacy"])

    assert result.exit_code == 0, result.output
    assert all(not path.exists() for path in owned_files)
    assert keep.read_text(encoding="utf-8") == "current"
    assert app.is_dir()
    assert old_plist.read_bytes() == b"old plist"


def test_clean_legacy_does_not_follow_symlinked_data_subdirectory(tmp_path):
    data_dir = tmp_path / ".penpal"
    data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_log = outside / "keypulse-old.log"
    outside_log.write_text("keep", encoding="utf-8")
    (data_dir / "logs").symlink_to(outside, target_is_directory=True)

    assert maintenance_cli.clean_legacy_data_files(data_dir) == []
    assert outside_log.read_text(encoding="utf-8") == "keep"


def test_migration_marker_directory_is_not_reported_as_clutter(tmp_path):
    home = tmp_path / "home"
    data_dir = home / ".penpal"
    data_dir.mkdir(parents=True)
    legacy_dir = home / ".keypulse"
    legacy_dir.mkdir()
    (legacy_dir / MIGRATION_MARKER).write_text(
        f"migrated_at=2026-01-01T00:00:00+00:00\nnew_path={data_dir}\n", encoding="utf-8")

    artifacts = find_legacy_artifacts(home, [], home / "Library" / "LaunchAgents", data_dir)

    assert [item for item in artifacts if item["kind"] == "legacy_data_dir"] == []
