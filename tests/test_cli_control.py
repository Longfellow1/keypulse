from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from penpal.cli import main


def test_stop_uses_launchd_bootout_when_supervised(monkeypatch, tmp_path):
    plist_path = tmp_path / "com.penpal.daemon.plist"
    plist_path.write_text("plist")
    calls = []

    class FakeLock:
        def get_pid(self):
            return 4242

        def is_running(self):
            return False

    monkeypatch.setattr("penpal.cli._launchd_daemon_plist_path", lambda: plist_path)
    monkeypatch.setattr("penpal.cli._launchd_label_loaded", lambda label: True)
    monkeypatch.setattr("penpal.cli._launchd_bootout", lambda path: True)
    monkeypatch.setattr("penpal.cli.SingleInstanceLock", lambda: FakeLock())
    monkeypatch.setattr("penpal.cli.os.kill", lambda pid, sig: calls.append((pid, sig)))

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0
    assert calls == [(4242, 15)]
    assert "launchd supervision disabled" in result.output


def test_start_uses_launchd_bootstrap_when_plist_exists(monkeypatch, tmp_path):
    plist_path = tmp_path / "com.penpal.daemon.plist"
    plist_path.write_text("plist")

    class FakeLock:
        def get_pid(self):
            return 4242

        def is_running(self):
            return False

    monkeypatch.setattr("penpal.cli._launchd_daemon_plist_path", lambda: plist_path)
    monkeypatch.setattr("penpal.cli._launchd_label_loaded", lambda label: False)
    monkeypatch.setattr("penpal.cli._launchd_bootstrap", lambda path: True)
    monkeypatch.setattr("penpal.cli.SingleInstanceLock", lambda: FakeLock())

    result = CliRunner().invoke(main, ["start"])

    assert result.exit_code == 0
    assert "started via launchd" in result.output


def test_status_plain_includes_runtime_state_and_watcher_component(monkeypatch, tmp_path):
    db_path = tmp_path / "penpal.db"
    db_path.write_text("db")

    cfg = type("Cfg", (), {"db_path_expanded": db_path, "watchers": type("Watchers", (), {
        "window": True,
        "idle": False,
        "clipboard": False,
        "manual": False,
        "browser": False,
        "ax_text": True,
        "ocr": True,
    })()})()

    monkeypatch.setattr("penpal.cli.get_config", lambda: cfg)
    monkeypatch.setattr("penpal.cli.require_db", lambda cfg: None)
    from penpal.runtime import RuntimeState, RuntimeStatus
    from penpal.runtime.record import Component
    monkeypatch.setattr("penpal.cli.evaluate", lambda: RuntimeStatus(RuntimeState.RUNNING, pid=4242, components={"ax_text": Component("running")}))
    monkeypatch.setattr("penpal.cli.get_state", lambda key: {
        "status": "running",
        "started_at": "2026-04-19T10:00:00+00:00",
        "last_flush": "2026-04-19T10:00:05+00:00",
    }.get(key))
    monkeypatch.setattr("penpal.cli.disabled_sources", lambda: frozenset())
    monkeypatch.setattr("penpal.sources.importer.progress_rows", lambda: [])
    monkeypatch.setattr("penpal.sources.history_gate.historical_imports_allowed", lambda: True)

    result = CliRunner().invoke(main, ["status", "--plain"])

    assert result.exit_code == 0
    assert "runtime_state=running" in result.output
    assert "runtime_ax_running=True" in result.output


def test_doctor_plain_includes_runtime_watcher_checks(monkeypatch, tmp_path):
    cfg = type("Cfg", (), {"db_path_expanded": tmp_path / "penpal.db", "watchers": type("W", (), {"keyboard_chunk": True})()})()
    cfg.db_path_expanded.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("penpal.cli.get_config", lambda: cfg)
    monkeypatch.setattr("penpal.cli.get_config_path", lambda: tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text("x=1")
    from penpal.runtime import RuntimeState, RuntimeStatus
    from penpal.runtime.record import Component
    monkeypatch.setattr("penpal.cli.evaluate", lambda: RuntimeStatus(RuntimeState.RUNNING, components={"ax_text": Component("running")}))
    monkeypatch.setattr("penpal.cli._stale_moved_dev_venv_script", lambda: None)
    monkeypatch.setattr("penpal.cli._legacy_mcp_config_findings", lambda: [])
    monkeypatch.setattr("penpal.cli._launchd_label_loaded", lambda _label: False)
    monkeypatch.setattr("penpal.health.capture_health.permission_report", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("penpal.sources.onboarding.source_problems", lambda _names: [])
    monkeypatch.setattr("penpal.sources.registry.list_sources", lambda: [])

    result = CliRunner().invoke(main, ["doctor", "--plain"])

    assert "后台正文采集线程: OK" in result.output
    # OCR is disabled: shown as information, never as a failing thread check.
    assert "后台屏幕识别线程" not in result.output
    assert "屏幕识别（OCR）: DISABLED (OCR off)" in result.output
