from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from click.testing import CliRunner

from keypulse.cli import main
from keypulse.store.db import close, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


def _utc_iso(delta: timedelta = timedelta(0)) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


def _make_config(db_path: Path, vault_path: Path, **watcher_flags):
    watchers = {
        "window": False,
        "idle": False,
        "clipboard": False,
        "manual": False,
        "browser": False,
        "ax_text": False,
        "keyboard_chunk": False,
        "ocr": False,
    }
    watchers.update(watcher_flags)
    return type(
        "Cfg",
        (),
        {
            "db_path_expanded": db_path,
            "obsidian": type("ObsidianCfg", (), {"vault_path": str(vault_path)})(),
            "watchers": type("WatchersCfg", (), watchers)(),
        },
    )()


def _seed_event(db_path: Path, *, source: str, event_type: str, ts_start: str, speaker: str = "system") -> None:
    init_db(db_path)
    insert_raw_event(
        RawEvent(
            source=source,
            event_type=event_type,
            ts_start=ts_start,
            speaker=speaker,
        )
    )


def test_healthcheck_reports_alive_daemon_and_writes_atomic_json(monkeypatch, tmp_path):
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    daily_dir = vault_path / "Daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-04-22.md").write_text("daily note")
    config = _make_config(db_path, vault_path, window=True)
    out_path = tmp_path / "health.json"

    class FakeRun:
        returncode = 0
        stdout = "PID = 69943\n"
        stderr = ""

    kills: list[tuple[int, int]] = []

    monkeypatch.setattr("keypulse.health.check.Config.load", lambda: config)
    monkeypatch.setattr("keypulse.health.check.HEALTH_JSON_PATH", out_path)
    monkeypatch.setattr("keypulse.health.check.subprocess.run", lambda *args, **kwargs: FakeRun())
    monkeypatch.setattr("keypulse.health.check.os.kill", lambda pid, sig: kills.append((pid, sig)))

    result = __import__("keypulse.health.check", fromlist=["run_healthcheck"]).run_healthcheck()

    assert result["overall"] == "ok"
    assert result["daemon"]["alive"] is True
    assert result["daemon"]["pid"] == 69943
    assert result["integrity"]["key_watchers_enabled"] == ["window"]
    assert out_path.exists()
    assert json.loads(out_path.read_text()) == result
    assert not out_path.with_name(out_path.name + ".tmp").exists()
    assert kills == [(69943, 0)]


def test_healthcheck_marks_dead_daemon_when_launchctl_pid_missing(monkeypatch, tmp_path):
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    config = _make_config(db_path, vault_path, window=True)
    out_path = tmp_path / "health.json"

    class FakeRun:
        returncode = 0
        stdout = "something without pid"
        stderr = ""

    monkeypatch.setattr("keypulse.health.check.Config.load", lambda: config)
    monkeypatch.setattr("keypulse.health.check.HEALTH_JSON_PATH", out_path)
    monkeypatch.setattr("keypulse.health.check.subprocess.run", lambda *args, **kwargs: FakeRun())
    monkeypatch.setattr(
        "keypulse.health.check.os.kill",
        lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()) if sig == 0 else None,
    )

    result = __import__("keypulse.health.check", fromlist=["run_healthcheck"]).run_healthcheck()

    assert result["daemon"]["alive"] is False
    assert any(alert["code"] == "DAEMON_DEAD" for alert in result["alerts"])


def test_healthcheck_detects_stale_stream_and_kills_daemon(monkeypatch, tmp_path):
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    daily_dir = vault_path / "Daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-04-22.md").write_text("daily note")
    config = _make_config(db_path, vault_path, window=True)
    out_path = tmp_path / "health.json"
    stale_ts = _utc_iso(timedelta(minutes=11))

    _seed_event(db_path, source="window", event_type="window_focus", ts_start=stale_ts)

    class FakeRun:
        returncode = 0
        stdout = "PID = 11111\n"
        stderr = ""

    kills: list[tuple[int, int]] = []

    monkeypatch.setattr("keypulse.health.check.Config.load", lambda: config)
    monkeypatch.setattr("keypulse.health.check.HEALTH_JSON_PATH", out_path)
    monkeypatch.setattr("keypulse.health.check.subprocess.run", lambda *args, **kwargs: FakeRun())
    monkeypatch.setattr("keypulse.health.check.os.kill", lambda pid, sig: kills.append((pid, sig)))

    result = __import__("keypulse.health.check", fromlist=["run_healthcheck"]).run_healthcheck()

    assert result["daemon"]["alive"] is True
    assert result["daemon"]["events_last_10min"] == 0
    assert result["daemon"]["age_sec_since_last_event"] >= 660
    assert any(alert["code"] == "STALE_EVENT_STREAM" for alert in result["alerts"])
    assert kills == [(11111, 0), (11111, 9)]


def test_healthcheck_ignores_stale_stream_when_idle_recent(monkeypatch, tmp_path):
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    daily_dir = vault_path / "Daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-04-22.md").write_text("daily note")
    config = _make_config(db_path, vault_path, window=True)
    out_path = tmp_path / "health.json"

    _seed_event(db_path, source="window", event_type="window_focus", ts_start=_utc_iso(timedelta(minutes=11)))
    _seed_event(db_path, source="idle", event_type="idle_start", ts_start=_utc_iso(timedelta(minutes=5)))

    class FakeRun:
        returncode = 0
        stdout = "PID = 11111\n"
        stderr = ""

    monkeypatch.setattr("keypulse.health.check.Config.load", lambda: config)
    monkeypatch.setattr("keypulse.health.check.HEALTH_JSON_PATH", out_path)
    monkeypatch.setattr("keypulse.health.check.subprocess.run", lambda *args, **kwargs: FakeRun())
    monkeypatch.setattr("keypulse.health.check.os.kill", lambda *args, **kwargs: None)

    result = __import__("keypulse.health.check", fromlist=["run_healthcheck"]).run_healthcheck()

    assert result["overall"] == "ok"
    assert not any(alert["code"] == "STALE_EVENT_STREAM" for alert in result["alerts"])


def test_healthcheck_flags_speaker_mislabel_spike_and_ignores_low_ratio(monkeypatch, tmp_path):
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    daily_dir = vault_path / "Daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "2026-04-22.md").write_text("daily note")
    out_path = tmp_path / "health.json"

    high_ratio_config = _make_config(db_path, vault_path, window=True)

    for index in range(10):
        _seed_event(
            db_path,
            source="clipboard",
            event_type="clipboard_copy",
            ts_start=_utc_iso(timedelta(minutes=1, seconds=index)),
            speaker="system" if index < 3 else "user",
        )

    class FakeRun:
        returncode = 0
        stdout = "PID = 22222\n"
        stderr = ""

    monkeypatch.setattr("keypulse.health.check.Config.load", lambda: high_ratio_config)
    monkeypatch.setattr("keypulse.health.check.HEALTH_JSON_PATH", out_path)
    monkeypatch.setattr("keypulse.health.check.subprocess.run", lambda *args, **kwargs: FakeRun())
    monkeypatch.setattr("keypulse.health.check.os.kill", lambda *args, **kwargs: None)

    result = __import__("keypulse.health.check", fromlist=["run_healthcheck"]).run_healthcheck()
    assert result["integrity"]["speaker_ratio_system_in_user_source_last_1h"] > 0.10
    assert any(alert["code"] == "SPEAKER_MISLABEL_SPIKE" for alert in result["alerts"])

    close()
    low_ratio_db = tmp_path / "low-ratio.db"
    low_ratio_config = _make_config(low_ratio_db, vault_path, window=True)
    low_out = tmp_path / "health-low.json"

    for index in range(50):
        _seed_event(
            low_ratio_db,
            source="clipboard",
            event_type="clipboard_copy",
            ts_start=_utc_iso(timedelta(minutes=1, seconds=index)),
            speaker="system" if index == 0 else "user",
        )

    monkeypatch.setattr("keypulse.health.check.Config.load", lambda: low_ratio_config)
    monkeypatch.setattr("keypulse.health.check.HEALTH_JSON_PATH", low_out)

    low_result = __import__("keypulse.health.check", fromlist=["run_healthcheck"]).run_healthcheck()
    assert low_result["integrity"]["speaker_ratio_system_in_user_source_last_1h"] == 0.02
    assert not any(alert["code"] == "SPEAKER_MISLABEL_SPIKE" for alert in low_result["alerts"])


def test_healthcheck_reports_config_load_failure(monkeypatch, tmp_path):
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    config = _make_config(db_path, vault_path, window=True)
    out_path = tmp_path / "health.json"

    class FakeRun:
        returncode = 0
        stdout = "PID = 33333\n"
        stderr = ""

    monkeypatch.setattr("keypulse.health.check.Config.load", lambda: (_ for _ in ()).throw(RuntimeError("bad config")))
    monkeypatch.setattr("keypulse.health.check.HEALTH_JSON_PATH", out_path)
    monkeypatch.setattr("keypulse.health.check.subprocess.run", lambda *args, **kwargs: FakeRun())
    monkeypatch.setattr("keypulse.health.check.os.kill", lambda *args, **kwargs: None)

    result = __import__("keypulse.health.check", fromlist=["run_healthcheck"]).run_healthcheck()

    assert result["integrity"]["config_loadable"] is False
    assert any(alert["code"] == "CONFIG_LOAD_FAILED" for alert in result["alerts"])


def test_healthcheck_flags_missing_watchers(monkeypatch, tmp_path):
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    config = _make_config(db_path, vault_path)
    out_path = tmp_path / "health.json"

    class FakeRun:
        returncode = 0
        stdout = "PID = 44444\n"
        stderr = ""

    monkeypatch.setattr("keypulse.health.check.Config.load", lambda: config)
    monkeypatch.setattr("keypulse.health.check.HEALTH_JSON_PATH", out_path)
    monkeypatch.setattr("keypulse.health.check.subprocess.run", lambda *args, **kwargs: FakeRun())
    monkeypatch.setattr("keypulse.health.check.os.kill", lambda *args, **kwargs: None)

    result = __import__("keypulse.health.check", fromlist=["run_healthcheck"]).run_healthcheck()

    assert result["integrity"]["key_watchers_enabled"] == []
    assert any(alert["code"] == "CRITICAL_WATCHERS_DISABLED" for alert in result["alerts"])


def test_launchctl_pid_parses_quoted_key():
    from keypulse.health.check import _launchctl_pid
    import keypulse.health.check as check_mod

    class FakeResult:
        returncode = 0
        stdout = '{\n\t"Label" = "com.keypulse.daemon";\n\t"PID" = 69943;\n};\n'
        stderr = ""

    original = check_mod.subprocess.run
    check_mod.subprocess.run = lambda *a, **kw: FakeResult()
    try:
        assert _launchctl_pid() == 69943
    finally:
        check_mod.subprocess.run = original


def test_healthcheck_cli_exit_policy(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "keypulse.health.run_healthcheck",
        lambda config_path=None: {
            "schema_version": 1,
            "checked_at": "2026-04-22T10:50:00+00:00",
            "overall": "alert",
            "daemon": {"alive": True, "pid": 1, "last_event_at": None, "events_last_10min": 0, "age_sec_since_last_event": None},
            "integrity": {"schema_version": 11, "config_loadable": True, "key_watchers_enabled": ["window"], "speaker_ratio_system_in_user_source_last_1h": 0.02},
            "sync": {"last_daily_sync_at": None, "last_daily_file_date": None},
            "alerts": [{"severity": "warn", "code": "ONLY_WARN", "message": "warn"}],
        },
    )

    result = CliRunner().invoke(main, ["healthcheck"])
    assert result.exit_code == 0

    monkeypatch.setattr(
        "keypulse.health.run_healthcheck",
        lambda config_path=None: {
            "schema_version": 1,
            "checked_at": "2026-04-22T10:50:00+00:00",
            "overall": "alert",
            "daemon": {"alive": False, "pid": None, "last_event_at": None, "events_last_10min": 0, "age_sec_since_last_event": None},
            "integrity": {"schema_version": 11, "config_loadable": False, "key_watchers_enabled": [], "speaker_ratio_system_in_user_source_last_1h": 0.0},
            "sync": {"last_daily_sync_at": None, "last_daily_file_date": None},
            "alerts": [{"severity": "error", "code": "BAD", "message": "bad"}],
        },
    )

    result = CliRunner().invoke(main, ["healthcheck"])
    assert result.exit_code == 1
