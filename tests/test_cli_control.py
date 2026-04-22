from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from keypulse.cli import main


def test_stop_uses_launchd_bootout_when_supervised(monkeypatch, tmp_path):
    plist_path = tmp_path / "com.keypulse.daemon.plist"
    plist_path.write_text("plist")
    calls = []

    class FakeLock:
        def get_pid(self):
            return 4242

        def is_running(self):
            return False

    monkeypatch.setattr("keypulse.cli._launchd_daemon_plist_path", lambda: plist_path)
    monkeypatch.setattr("keypulse.cli._launchd_label_loaded", lambda label: True)
    monkeypatch.setattr("keypulse.cli._launchd_bootout", lambda path: True)
    monkeypatch.setattr("keypulse.cli.SingleInstanceLock", lambda: FakeLock())
    monkeypatch.setattr("keypulse.cli.os.kill", lambda pid, sig: calls.append((pid, sig)))

    result = CliRunner().invoke(main, ["stop"])

    assert result.exit_code == 0
    assert calls == [(4242, 15)]
    assert "launchd supervision disabled" in result.output


def test_start_uses_launchd_bootstrap_when_plist_exists(monkeypatch, tmp_path):
    plist_path = tmp_path / "com.keypulse.daemon.plist"
    plist_path.write_text("plist")

    class FakeLock:
        def get_pid(self):
            return 4242

        def is_running(self):
            return False

    monkeypatch.setattr("keypulse.cli._launchd_daemon_plist_path", lambda: plist_path)
    monkeypatch.setattr("keypulse.cli._launchd_label_loaded", lambda label: False)
    monkeypatch.setattr("keypulse.cli._launchd_bootstrap", lambda path: True)
    monkeypatch.setattr("keypulse.cli.SingleInstanceLock", lambda: FakeLock())

    result = CliRunner().invoke(main, ["start"])

    assert result.exit_code == 0
    assert "started via launchd" in result.output


def test_status_plain_includes_runtime_capture_metrics(monkeypatch, tmp_path):
    db_path = tmp_path / "keypulse.db"
    db_path.write_text("db")

    class FakeLock:
        def get_pid(self):
            return 4242

    cfg = type("Cfg", (), {"db_path_expanded": db_path, "watchers": type("Watchers", (), {
        "window": True,
        "idle": False,
        "clipboard": False,
        "manual": False,
        "browser": False,
        "ax_text": True,
        "keyboard_chunk": True,
        "ocr": True,
    })()})()

    monkeypatch.setattr("keypulse.cli.get_config", lambda: cfg)
    monkeypatch.setattr("keypulse.cli.require_db", lambda cfg: None)
    monkeypatch.setattr("keypulse.cli.SingleInstanceLock", lambda: FakeLock())
    monkeypatch.setattr("keypulse.cli._launchd_label_loaded", lambda label: True)
    monkeypatch.setattr("keypulse.cli.get_state", lambda key: {
        "status": "running",
        "started_at": "2026-04-19T10:00:00+00:00",
        "last_flush": "2026-04-19T10:00:05+00:00",
    }.get(key))
    monkeypatch.setattr("keypulse.cli._load_capture_runtime_state", lambda: {
        "pid": 4242,
        "host_executable": "/usr/local/bin/python3",
        "watchers": {
            "ax_text": {"running": True},
            "ocr": {"running": True},
            "keyboard_chunk": {"source": {"status": "running"}},
        },
        "multi_source_counts": {
            "ax_text": 3,
            "ocr_text": 1,
            "keyboard_chunk": 2,
        },
    })

    result = CliRunner().invoke(main, ["status", "--plain"])

    assert result.exit_code == 0
    assert "runtime_ax_running=True" in result.output
    assert "runtime_host=/usr/local/bin/python3" in result.output
    assert "runtime_ocr_count=1" in result.output
    assert "runtime_keyboard_count=2" in result.output


def test_doctor_plain_includes_runtime_watcher_checks(monkeypatch, tmp_path):
    cfg = type("Cfg", (), {"db_path_expanded": tmp_path / "keypulse.db"})()
    cfg.db_path_expanded.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("keypulse.cli.get_config", lambda: cfg)
    monkeypatch.setattr("keypulse.cli.get_config_path", lambda: tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text("x=1")
    monkeypatch.setattr("keypulse.cli._load_capture_runtime_state", lambda: {
        "watchers": {
            "ax_text": {"running": True},
            "ocr": {"running": True},
            "keyboard_chunk": {"source": {"status": "running"}},
        }
    })

    result = CliRunner().invoke(main, ["doctor", "--plain"])

    assert "后台正文采集线程: OK" in result.output
    assert "后台屏幕识别线程: OK" in result.output
    assert "后台键盘监听线程: OK" in result.output
