"""Headless failure visibility (D33): MCP `capture_health` field and `penpal doctor` permissions.

Fakes only: temp DB and HOME, permission checks replaced by stubs (nothing here can prompt).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from penpal import permissions
from penpal.capabilities.base import HealthState
from penpal.capabilities.store import save_states
from penpal.config import Config
from penpal.health import capture_health as ch
from penpal.mcp import server
from penpal.permissions import PermissionSpec, PermissionStatus
from penpal.store.db import init_db
from penpal.store.repository import set_state


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    init_db(tmp_path / "kp.db")
    ch.reset_cache()
    yield
    ch.reset_cache()


def _heartbeat(age_sec=1, *, running=True, storage=None):
    captured = datetime.now(timezone.utc) - timedelta(seconds=age_sec)
    payload = {"captured_at": captured.isoformat(), "running": running, "watchers": {}}
    if storage:
        payload["storage"] = storage
    set_state("capture_runtime", json.dumps(payload))
    set_state("status", "running" if running else "stopped")


def _ax_denied():
    save_states({"accessibility_permission": HealthState(ok=False, code="ax_denied", last_checked=0.0)})


def _cfg(**watchers):
    cfg = Config()
    for name in ("keyboard_chunk", "ax_text", "ocr"):
        setattr(cfg.watchers, name, False)
    for name, value in watchers.items():
        setattr(cfg.watchers, name, value)
    return cfg


# ── MCP capture_health ──────────────────────────────────────────────────────────

def test_healthy_has_no_field():
    _heartbeat()
    assert ch.compute_capture_health(_cfg()) is None


def test_result_unchanged_when_healthy(monkeypatch):
    monkeypatch.setattr(server, "current_capture_health", ch.current_capture_health)
    _heartbeat()
    result = server._result({"status": "ok", "results": []})
    assert "capture_health" not in result.structuredContent
    assert result.structuredContent == {"status": "ok", "results": []}


def test_daemon_stopped_gives_code_and_sentence(monkeypatch):
    monkeypatch.setattr(server, "current_capture_health", ch.current_capture_health)
    _heartbeat(35 * 60, running=False)
    health = server._result({"status": "ok"}).structuredContent["capture_health"]
    assert health["code"] == "daemon_not_running"
    assert "35 min" in health["message"] and "penpal start" in health["message"]


def test_no_heartbeat_ever_means_daemon_not_running():
    assert ch.compute_capture_health(_cfg())["code"] == "daemon_not_running"


def test_stale_heartbeat_while_marked_running():
    _heartbeat(ch.HEARTBEAT_STALE_SEC + 60)
    assert ch.compute_capture_health(_cfg())["code"] == "heartbeat_stale"


def test_fresh_heartbeat_below_threshold_is_healthy():
    _heartbeat(ch.HEARTBEAT_STALE_SEC - 120)
    assert ch.compute_capture_health(_cfg()) is None


def test_paused_gives_field():
    _heartbeat()
    set_state("status", "paused")
    health = ch.compute_capture_health(_cfg())
    assert health["code"] == "capture_paused" and "penpal resume" in health["message"]


def test_disk_full_gives_field():
    _heartbeat(storage={"status": "error", "reason": "disk_full", "dropped_events": 3})
    health = ch.compute_capture_health(_cfg())
    assert health["code"] == "disk_full" and "disk is full" in health["message"]


def test_missing_permission_for_enabled_source_gives_field():
    _heartbeat()
    _ax_denied()
    health = ch.compute_capture_health(_cfg(ax_text=True))
    assert health["code"] == "permission_missing" and "ax_text" in health["message"]


def test_missing_permission_ignored_when_source_not_enabled():
    _heartbeat()
    _ax_denied()
    assert ch.compute_capture_health(_cfg()) is None


def test_missing_permission_ignored_when_source_switched_off():
    _heartbeat()
    _ax_denied()
    set_state("disabled_sources", json.dumps(["ax_text"]))
    assert ch.compute_capture_health(_cfg(ax_text=True)) is None


def test_permission_denied_response_never_carries_field(monkeypatch):
    monkeypatch.setattr(server, "current_capture_health", lambda: {"code": "x", "message": "y"})
    assert "capture_health" not in server._result({"status": "permission_denied"}).structuredContent


def test_cached_for_30_seconds(monkeypatch):
    calls = []
    monkeypatch.setattr(ch, "compute_capture_health", lambda: calls.append(1) or None)
    ch.current_capture_health()
    ch.current_capture_health()
    assert len(calls) == 1
    ch.reset_cache()
    ch.current_capture_health()
    assert len(calls) == 2


def test_reader_failure_is_not_an_alarm(monkeypatch):
    def boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(ch, "compute_capture_health", boom)
    assert ch.current_capture_health() is None


def test_tool_descriptions_tell_hosts_to_relay():
    for text in (server.INSTRUCTIONS, server.SEARCH_DESCRIPTION, server.EVIDENCE_DESCRIPTION,
                 server.TIMELINE_DESCRIPTION, server.RATE_DESCRIPTION):
        assert "capture_health" in text


# ── doctor ──────────────────────────────────────────────────────────────────────

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


G, D = PermissionStatus.GRANTED, PermissionStatus.DENIED


def test_report_lists_granted_and_missing_with_binaries(monkeypatch):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": D, "screen_recording": D})
    findings = {f.key: f for f in ch.permission_report(_cfg(keyboard_chunk=True), disabled=frozenset(), daemon_denied=set())}
    assert set(findings) == {"accessibility", "input_monitoring"}  # screen recording: OCR off, not required
    assert findings["accessibility"].granted and findings["accessibility"].status == "granted"
    missing = findings["input_monitoring"]
    assert not missing.granted and missing.status == "missing"
    assert missing.settings_pane.endswith("Privacy_ListenEvent")
    import os, sys
    assert os.path.realpath(sys.executable) in missing.executables


def test_report_daemon_denial_overrides_local_grant(monkeypatch):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": G, "screen_recording": G})
    findings = ch.permission_report(_cfg(ax_text=True), disabled=frozenset(), daemon_denied={"accessibility"})
    assert [f.granted for f in findings] == [False]


def _run_doctor(monkeypatch, tmp_path):
    from penpal import cli

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = _cfg(keyboard_chunk=True)
    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    monkeypatch.setattr(cli, "get_config_path", lambda: tmp_path / "config.toml")
    (tmp_path / "config.toml").write_text("x=1")
    return CliRunner().invoke(cli.main, ["doctor", "--plain"])


def test_doctor_fails_on_missing_permission_and_names_binary_and_pane(monkeypatch, tmp_path):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": D, "screen_recording": G})
    result = _run_doctor(monkeypatch, tmp_path)
    assert result.exit_code == 1
    failing = [line for line in result.output.splitlines() if line.endswith(": FAIL")]
    assert any("Input Monitoring" in line for line in failing)
    assert "Privacy_ListenEvent" in result.output
    import os, sys
    assert os.path.realpath(sys.executable) in result.output


def test_doctor_permission_items_pass_when_granted(monkeypatch, tmp_path):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": G, "screen_recording": G})
    result = _run_doctor(monkeypatch, tmp_path)
    perm_lines = [line for line in result.output.splitlines() if "权限" in line and ": " in line]
    assert perm_lines and all(line.endswith(": OK") for line in perm_lines)


def test_doctor_reports_moved_venv_with_old_absolute_shebang(monkeypatch, tmp_path):
    _fake_specs(monkeypatch, {"accessibility": G, "input_monitoring": G, "screen_recording": G})
    entry = tmp_path / ".penpal" / "venv" / "bin" / "penpal"
    entry.parent.mkdir(parents=True)
    entry.write_text(f"#!{tmp_path}/.keypulse/venv/bin/python\n", encoding="utf-8")

    result = _run_doctor(monkeypatch, tmp_path)

    assert "开发 venv 入口仍指向旧目录: FAIL" in result.output
    assert "run `bash install.sh --dev` to rebuild" in result.output
