"""Tests for capabilities added in the gateway-resilience refactor:
   - WatcherHealthCapability (generic, parameterized)
   - ScreenRecordingPermissionCapability
   - ObsidianSyncFreshnessCapability
   - LLMBackendCapability circuit-breaker integration
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from keypulse.capabilities.builtin._watcher_health import (
    WATCHER_HEALTH_SPECS,
    WatcherHealthCapability,
    WatcherHealthSpec,
)
from keypulse.capabilities.builtin.llm_backend import LLMBackendCapability
from keypulse.capabilities.builtin.obsidian_sync_freshness import ObsidianSyncFreshnessCapability
from keypulse.capabilities.builtin.screen_recording_permission import ScreenRecordingPermissionCapability


# ---- WatcherHealthCapability ---------------------------------------------------


def test_watcher_health_capability_ok_when_no_runtime(monkeypatch):
    """Without capture_runtime state set, capability is OK (not a failure)."""
    monkeypatch.setattr(
        "keypulse.capabilities.builtin._watcher_health.get_state",
        lambda _key: "",
    )
    spec = WatcherHealthSpec(watcher_name="browser", capability_name="watcher_browser")
    cap = WatcherHealthCapability(spec)
    state = cap.monitor()
    assert state.ok
    assert state.code == "ok"


def test_watcher_health_capability_flags_gave_up(monkeypatch):
    """When watcher.health.gave_up=True, capability is failing."""
    runtime = {"watchers": {"browser": {"gave_up": True, "heartbeat_gave_up": False}}}
    monkeypatch.setattr(
        "keypulse.capabilities.builtin._watcher_health.get_state",
        lambda _key: json.dumps(runtime),
    )
    spec = WatcherHealthSpec(watcher_name="browser", capability_name="watcher_browser")
    cap = WatcherHealthCapability(spec)
    state = cap.monitor()
    assert not state.ok
    assert state.code == "browser_watcher_gave_up"


def test_watcher_health_capability_flags_heartbeat_dead(monkeypatch):
    """heartbeat_gave_up triggers a separate error code."""
    runtime = {"watchers": {"clipboard": {"gave_up": False, "heartbeat_gave_up": True}}}
    monkeypatch.setattr(
        "keypulse.capabilities.builtin._watcher_health.get_state",
        lambda _key: json.dumps(runtime),
    )
    spec = WatcherHealthSpec(watcher_name="clipboard", capability_name="clipboard_watcher")
    cap = WatcherHealthCapability(spec)
    state = cap.monitor()
    assert not state.ok
    assert state.code == "clipboard_heartbeat_gave_up"


def test_watcher_health_capability_flags_silent_timeout(monkeypatch):
    """A running watcher that has not emitted for too long is unhealthy."""
    runtime = {
        "watchers": {
            "ax_text": {
                "running": True,
                "paused": False,
                "crashes": 0,
                "last_error": None,
                "gave_up": False,
                "heartbeat_gave_up": False,
                "last_emit_age_sec": 1900.0,
            }
        }
    }
    monkeypatch.setattr(
        "keypulse.capabilities.builtin._watcher_health.get_state",
        lambda _key: json.dumps(runtime),
    )
    spec = WatcherHealthSpec(
        watcher_name="ax_text",
        capability_name="ax_text_watcher",
        silent_timeout_sec=1800.0,
    )
    cap = WatcherHealthCapability(spec)
    state = cap.monitor()
    assert not state.ok
    assert state.code == "ax_text_silent_timeout"


def test_watcher_health_specs_include_browser_url() -> None:
    by_name = {spec.watcher_name: spec for spec in WATCHER_HEALTH_SPECS}
    browser_url = by_name.get("browser_url")
    assert browser_url is not None
    assert browser_url.capability_name == "browser_url_watcher"


@pytest.mark.skip(reason="OCR watcher disabled 2026-05-14")
def test_watcher_health_specs_cover_real_watchers():
    """The manifest must include every watcher we ship heartbeat for."""
    watcher_names = {spec.watcher_name for spec in WATCHER_HEALTH_SPECS}
    # All watchers with HEARTBEAT_TIMEOUT_SEC set
    expected = {"clipboard", "browser", "window", "ax_text", "ocr"}
    assert expected.issubset(watcher_names), (
        f"manifest missing: {expected - watcher_names}"
    )


# ---- ScreenRecordingPermissionCapability ---------------------------------------


def test_screen_recording_capability_ok_when_granted(monkeypatch):
    """When CGPreflightScreenCaptureAccess returns True, capability is OK."""
    import sys
    import types

    fake_quartz = types.ModuleType("Quartz")
    fake_quartz.CGPreflightScreenCaptureAccess = lambda: True  # type: ignore
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)

    cap = ScreenRecordingPermissionCapability()
    state = cap.monitor()
    assert state.ok
    assert state.code == "ok"


def test_screen_recording_capability_flags_denied(monkeypatch):
    """When preflight returns False, capability fails with action URL in diagnose."""
    import sys
    import types

    fake_quartz = types.ModuleType("Quartz")
    fake_quartz.CGPreflightScreenCaptureAccess = lambda: False  # type: ignore
    monkeypatch.setitem(sys.modules, "Quartz", fake_quartz)

    cap = ScreenRecordingPermissionCapability()
    state = cap.monitor()
    assert not state.ok
    assert state.code == "screen_capture_denied"

    signal = cap.diagnose(state)
    assert signal.action is not None
    assert "Privacy_ScreenCapture" in signal.action


# ---- ObsidianSyncFreshnessCapability -------------------------------------------


def _setup_test_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with the trigger table."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE llm_trigger_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            ts_utc TEXT NOT NULL,
            outcome TEXT NOT NULL,
            note TEXT DEFAULT ''
        )
        """
    )
    conn.commit()
    conn.close()
    return db


def _patch_config_db(monkeypatch, db_path: Path) -> None:
    from keypulse.capabilities.builtin import obsidian_sync_freshness

    class FakeCfg:
        @property
        def db_path_expanded(self):
            return db_path

    monkeypatch.setattr(obsidian_sync_freshness.Config, "load", staticmethod(lambda: FakeCfg()))


def test_obsidian_sync_freshness_ok_on_fresh_install(tmp_path, monkeypatch):
    """No trigger history → ok (could be a brand new install)."""
    db = _setup_test_db(tmp_path)
    _patch_config_db(monkeypatch, db)
    cap = ObsidianSyncFreshnessCapability()
    state = cap.monitor()
    assert state.ok


def test_obsidian_sync_freshness_flags_recent_failure(tmp_path, monkeypatch):
    """A `ran:fail` outcome within the past hour fails the capability."""
    db = _setup_test_db(tmp_path)
    conn = sqlite3.connect(str(db))
    recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn.execute(
        "INSERT INTO llm_trigger_log (kind, ts_utc, outcome, note) VALUES (?, ?, ?, ?)",
        ("T1", recent, "ran:fail", "503"),
    )
    conn.commit()
    conn.close()
    _patch_config_db(monkeypatch, db)

    cap = ObsidianSyncFreshnessCapability()
    state = cap.monitor()
    assert not state.ok
    assert state.code == "sync_recent_failure"


def test_obsidian_sync_freshness_quiet_when_user_idle(tmp_path, monkeypatch):
    """Stale sync but no recent capture activity → ok (user simply idle)."""
    db = _setup_test_db(tmp_path)
    conn = sqlite3.connect(str(db))
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    conn.execute(
        "INSERT INTO llm_trigger_log (kind, ts_utc, outcome, note) VALUES (?, ?, ?, ?)",
        ("T1", long_ago, "ran:ok", ""),
    )
    conn.commit()
    conn.close()
    _patch_config_db(monkeypatch, db)
    # No last_flush set → capture inactive → no alert
    monkeypatch.setattr(
        "keypulse.store.repository.get_state", lambda _key: ""
    )

    cap = ObsidianSyncFreshnessCapability()
    state = cap.monitor()
    assert state.ok


# ---- LLMBackendCapability circuit integration ----------------------------------


def test_llm_backend_capability_surfaces_active_circuit(tmp_path, monkeypatch):
    """When gateway state has an active short_circuit, capability fails."""
    state_file = tmp_path / "model-state.json"
    until = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    state_file.write_text(json.dumps({
        "short_circuits": {
            "cloud": {
                "until": until,
                "reason": "auth_failed_401",
                "fail_count": 3,
            }
        }
    }))

    class FakeModelCfg:
        active_profile = "local-only"
        state_path = str(state_file)

        class cloud:
            kind = "openai_compatible"
            api_key_env = ""

    class FakeCfg:
        model = FakeModelCfg()

    monkeypatch.setattr(LLMBackendCapability, "_state_code", lambda self: "")
    monkeypatch.setattr(
        "keypulse.capabilities.builtin.llm_backend.Config",
        type("FakeC", (), {"load": staticmethod(lambda: FakeCfg())}),
    )

    cap = LLMBackendCapability()
    state = cap.monitor()
    assert not state.ok
    assert state.code == "llm_circuit_open"
    assert "auth_failed_401" in (state.detail or "")
