"""Doctor reports the daemon's own permission state (launchd process), not the terminal it runs in.

Fakes only: temp HOME and DB, permission checks replaced by stubs (nothing here can prompt).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from penpal import app, permissions
from penpal.capabilities.registry import CapabilityRegistry
from penpal.config import Config
from penpal.health import capture_health as ch
from penpal.permissions import PermissionSpec, PermissionStatus
from penpal.store.db import init_db
from penpal.store.repository import get_state, set_state

G, D = PermissionStatus.GRANTED, PermissionStatus.DENIED


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    # doctor re-initialises the DB from its config path, so use that same path for the state reads.
    default_db = home / ".penpal" / "penpal.db"
    default_db.parent.mkdir(parents=True)
    init_db(default_db)
    yield


def _fake_specs(monkeypatch, status_by_key):
    def spec(key, label, pane):
        return PermissionSpec(
            key=key, label=label, settings_pane=pane,
            check=lambda k=key: status_by_key[k],
            request=lambda: pytest.fail("doctor must never trigger a permission prompt"),
        )

    ax = spec("accessibility", "辅助功能（Accessibility）", "x-apple.systempreferences:fake?Privacy_Accessibility")
    im = spec("input_monitoring", "输入监控（Input Monitoring）", "x-apple.systempreferences:fake?Privacy_ListenEvent")
    sr = spec("screen_recording", "屏幕录制（Screen Recording）", "x-apple.systempreferences:fake?Privacy_ScreenCapture")
    monkeypatch.setattr(permissions, "PERMISSION_SPECS", (ax, im))
    monkeypatch.setattr(permissions, "SCREEN_RECORDING_SPEC", sr)


def _cfg():
    cfg = Config()
    cfg.watchers.keyboard_chunk = True
    cfg.watchers.ax_text = False
    return cfg


def _run_doctor(monkeypatch, tmp_path, cfg):
    from penpal import cli

    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    monkeypatch.setattr(cli, "get_config_path", lambda: tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text("x=1")
    return CliRunner().invoke(cli.main, ["doctor", "--plain"])


# ── daemon side ─────────────────────────────────────────────────────────────────

def test_daemon_records_its_permissions_and_doctor_prefers_them(monkeypatch):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": D})
    snapshot = ch.record_daemon_permissions()
    assert snapshot["permissions"] == {"accessibility": "granted", "input_monitoring": "denied"}
    assert snapshot["host_executable"] == os.path.realpath(sys.executable)

    # The terminal disagrees with the daemon; the daemon's answer wins and says where it came from.
    _fake_specs(monkeypatch, {"accessibility": D, "input_monitoring": G})
    findings = {f.key: f for f in ch.permission_report(_cfg(), disabled=frozenset(), daemon_denied=set())}
    assert findings["accessibility"].checked_by == "daemon" and findings["accessibility"].granted
    assert findings["input_monitoring"].checked_by == "daemon" and not findings["input_monitoring"].granted
    assert findings["input_monitoring"].age_sec is not None and findings["input_monitoring"].age_sec < 60


def test_self_check_tick_records_daemon_permissions(monkeypatch):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": D})
    registry = CapabilityRegistry()
    monkeypatch.setattr(registry, "monitor_all", lambda: {})

    app._run_capability_self_check(registry)

    assert ch.daemon_permission_snapshot()["permissions"]["input_monitoring"] == "denied"


def test_stale_daemon_report_falls_back_to_terminal(monkeypatch):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": G})
    ch.record_daemon_permissions()
    raw = json.loads(get_state(ch.DAEMON_PERMISSIONS_KEY))
    raw["checked_at"] = (datetime.now(timezone.utc) - timedelta(seconds=ch.DAEMON_PERMISSION_MAX_AGE_SEC + 60)).isoformat()
    set_state(ch.DAEMON_PERMISSIONS_KEY, json.dumps(raw))

    _fake_specs(monkeypatch, {"accessibility": D, "input_monitoring": D})
    findings = ch.permission_report(_cfg(), disabled=frozenset(), daemon_denied=set())
    assert findings and all(f.checked_by == "terminal" and f.age_sec is None for f in findings)
    assert not any(f.granted for f in findings)


def test_no_daemon_record_uses_terminal_check(monkeypatch):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": G})
    findings = ch.permission_report(_cfg(), disabled=frozenset(), daemon_denied=set())
    assert findings and all(f.checked_by == "terminal" and f.granted for f in findings)


# ── permission scope and grant target ──────────────────────────────────────────

def test_grant_target_is_the_python_app_bundle_when_present(tmp_path):
    framework = tmp_path / "Python.framework" / "Versions" / "3.13"
    (framework / "bin").mkdir(parents=True)
    interpreter = framework / "bin" / "python3.13"
    interpreter.write_text("")
    real_framework = Path(os.path.realpath(framework))

    assert ch.grant_target(str(interpreter)) == os.path.realpath(interpreter)  # no bundle: the interpreter

    bundle = real_framework / "Resources" / "Python.app"
    bundle.mkdir(parents=True)
    assert ch.grant_target(str(interpreter)) == str(bundle)
    assert ch.executables_to_grant(str(interpreter))[0] == str(bundle)  # daemon's interpreter comes first


# ── doctor output ───────────────────────────────────────────────────────────────

def test_doctor_labels_daemon_report_and_shows_ocr_as_disabled(monkeypatch, tmp_path):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": G})
    ch.record_daemon_permissions()
    _fake_specs(monkeypatch, {"accessibility": D, "input_monitoring": D})  # this terminal is not granted

    result = _run_doctor(monkeypatch, tmp_path, _cfg())

    perm_lines = [line for line in result.output.splitlines() if "权限（" in line]
    assert len(perm_lines) == 2
    assert all("后台服务" in line and line.endswith(": OK") for line in perm_lines), perm_lines
    assert "屏幕录制" not in result.output
    assert "后台屏幕识别线程" not in result.output
    assert "屏幕识别（OCR）: DISABLED (OCR off)" in result.output


def test_doctor_without_daemon_report_says_it_checked_the_terminal(monkeypatch, tmp_path):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": D})

    result = _run_doctor(monkeypatch, tmp_path, _cfg())

    assert result.exit_code == 1
    assert any("非后台服务）: FAIL" in line for line in result.output.splitlines())
    assert "后台服务状态不可用" in result.output
    assert "屏幕录制" not in result.output
