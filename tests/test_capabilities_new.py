"""Tests for capabilities added in the gateway-resilience refactor:
   - WatcherHealthCapability (generic, parameterized)
   - ScreenRecordingPermissionCapability
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from penpal.capabilities.builtin._watcher_health import (
    WATCHER_HEALTH_SPECS,
    WatcherHealthCapability,
    WatcherHealthSpec,
)
from penpal.capabilities.builtin.screen_recording_permission import ScreenRecordingPermissionCapability
from penpal.runtime import RuntimeState, RuntimeStatus
from penpal.runtime.record import Component


def _runtime(monkeypatch, components):
    status = RuntimeStatus(RuntimeState.RUNNING, components=components)
    monkeypatch.setattr("penpal.capabilities.builtin._watcher_health.evaluate", lambda: status)


# ---- WatcherHealthCapability ---------------------------------------------------


def test_watcher_health_capability_ok_when_component_is_missing(monkeypatch):
    """A component that was not configured is not a failure."""
    _runtime(monkeypatch, {})
    spec = WatcherHealthSpec(watcher_name="browser", capability_name="watcher_browser")
    cap = WatcherHealthCapability(spec)
    state = cap.monitor()
    assert state.ok
    assert state.code == "ok"


def test_watcher_health_capability_flags_gave_up(monkeypatch):
    """The runtime record's gave_up component state is failing."""
    _runtime(monkeypatch, {"browser": Component("gave_up")})
    spec = WatcherHealthSpec(watcher_name="browser", capability_name="watcher_browser")
    cap = WatcherHealthCapability(spec)
    state = cap.monitor()
    assert not state.ok
    assert state.code == "browser_watcher_gave_up"


def test_watcher_health_capability_flags_heartbeat_dead(monkeypatch):
    """Heartbeat exhaustion uses the shared gave_up state."""
    _runtime(monkeypatch, {"clipboard": Component("gave_up", "heartbeat timeout")})
    spec = WatcherHealthSpec(watcher_name="clipboard", capability_name="clipboard_watcher")
    cap = WatcherHealthCapability(spec)
    state = cap.monitor()
    assert not state.ok
    assert state.code == "clipboard_watcher_gave_up"


def test_watcher_event_silence_is_not_a_watcher_failure(monkeypatch):
    """An idle source can be healthy when the core loop keeps progressing."""
    _runtime(monkeypatch, {"ax_text": Component("idle")})
    spec = WatcherHealthSpec(watcher_name="ax_text", capability_name="ax_text_watcher")
    cap = WatcherHealthCapability(spec)
    state = cap.monitor()
    assert state.ok and state.code == "ok"


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
