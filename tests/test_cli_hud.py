from __future__ import annotations

import signal

from click.testing import CliRunner

from keypulse.cli import main


def test_hud_invokes_hud_runner_by_default(monkeypatch, tmp_path):
    captured = {}
    pid_path = tmp_path / "hud.pid"

    def fake_load():
        return type("Cfg", (), {"db_path_expanded": object(), "log_path_expanded": object()})()

    def fake_require_db(cfg):
        captured["db_cfg"] = cfg

    def fake_run_hud(cfg):
        captured["hud_cfg"] = cfg

    monkeypatch.setattr("keypulse.cli.get_config", fake_load)
    monkeypatch.setattr("keypulse.cli.require_db", fake_require_db)
    monkeypatch.setattr("keypulse.cli.run_hud", fake_run_hud)
    monkeypatch.setattr("keypulse.cli._find_legacy_hud_pid", lambda: None)
    monkeypatch.setattr("keypulse.cli.get_hud_pid_path", lambda: pid_path)

    result = CliRunner().invoke(main, ["hud"])

    assert result.exit_code == 0
    assert "db_cfg" in captured
    assert "hud_cfg" in captured


def test_hud_start_invokes_hud_runner(monkeypatch, tmp_path):
    captured = {}
    pid_path = tmp_path / "hud.pid"

    def fake_load():
        return type("Cfg", (), {"db_path_expanded": object(), "log_path_expanded": object()})()

    def fake_require_db(cfg):
        captured["db_cfg"] = cfg

    def fake_run_hud(cfg):
        captured["hud_cfg"] = cfg

    monkeypatch.setattr("keypulse.cli.get_config", fake_load)
    monkeypatch.setattr("keypulse.cli.require_db", fake_require_db)
    monkeypatch.setattr("keypulse.cli.run_hud", fake_run_hud)
    monkeypatch.setattr("keypulse.cli._find_legacy_hud_pid", lambda: None)
    monkeypatch.setattr("keypulse.cli.get_hud_pid_path", lambda: pid_path)

    result = CliRunner().invoke(main, ["hud", "start"])

    assert result.exit_code == 0
    assert "db_cfg" in captured
    assert "hud_cfg" in captured


def test_hud_stop_terminates_running_instance(monkeypatch, tmp_path):
    pid_path = tmp_path / "hud.pid"
    pid_path.write_text("4242")
    calls = []

    def fake_get_hud_pid_path():
        return pid_path

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        if sig == signal.SIGTERM:
            pid_path.unlink(missing_ok=True)

    monkeypatch.setattr("keypulse.cli.get_hud_pid_path", fake_get_hud_pid_path)
    monkeypatch.setattr("keypulse.cli.os.kill", fake_kill)

    result = CliRunner().invoke(main, ["hud", "stop"])

    assert result.exit_code == 0
    assert (4242, signal.SIGTERM) in calls
    assert "stopped" in result.output.lower()


def test_hud_status_reports_running_instance(monkeypatch, tmp_path):
    pid_path = tmp_path / "hud.pid"
    pid_path.write_text("4242")

    monkeypatch.setattr("keypulse.cli.get_hud_pid_path", lambda: pid_path)
    monkeypatch.setattr("keypulse.cli.os.kill", lambda pid, sig: None)

    result = CliRunner().invoke(main, ["hud", "status", "--plain"])

    assert result.exit_code == 0
    assert "running=True" in result.output
    assert "pid=4242" in result.output


def test_hud_stop_falls_back_to_legacy_process_lookup(monkeypatch, tmp_path):
    pid_path = tmp_path / "hud.pid"
    calls = []

    monkeypatch.setattr("keypulse.cli.get_hud_pid_path", lambda: pid_path)
    monkeypatch.setattr("keypulse.cli._find_legacy_hud_pid", lambda: 5151)

    def fake_kill(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr("keypulse.cli.os.kill", fake_kill)

    result = CliRunner().invoke(main, ["hud", "stop"])

    assert result.exit_code == 0
    assert (5151, signal.SIGTERM) in calls


def test_hud_status_reports_legacy_process_when_pid_file_missing(monkeypatch, tmp_path):
    pid_path = tmp_path / "hud.pid"

    monkeypatch.setattr("keypulse.cli.get_hud_pid_path", lambda: pid_path)
    monkeypatch.setattr("keypulse.cli._find_legacy_hud_pid", lambda: 5151)

    result = CliRunner().invoke(main, ["hud", "status", "--plain"])

    assert result.exit_code == 0
    assert "running=True" in result.output
    assert "pid=5151" in result.output
