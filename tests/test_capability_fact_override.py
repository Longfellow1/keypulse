"""Fact-layer override on permission capabilities.

macOS preflight APIs (AXIsProcessTrusted / CGPreflightScreenCaptureAccess)
cache stale results in launchd subprocess context, causing false-negative
HUD red states even when capture watchers are healthy. These capabilities
must defer to the fact layer (capture_error_code + watcher runtime) when
probe reports False.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from keypulse.capabilities.builtin.accessibility_permission import (
    AccessibilityPermissionCapability,
)
from keypulse.capabilities.builtin.screen_recording_permission import (
    ScreenRecordingPermissionCapability,
)


def _mock_probe_denied_ax(monkeypatch) -> None:
    fake = types.ModuleType("ApplicationServices")
    fake.AXIsProcessTrusted = lambda: False  # type: ignore
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake)


def _mock_probe_denied_screen(monkeypatch) -> None:
    fake = types.ModuleType("Quartz")
    fake.CGPreflightScreenCaptureAccess = lambda: False  # type: ignore
    monkeypatch.setitem(sys.modules, "Quartz", fake)


def _mock_state(monkeypatch, *, capture_error_code: str = "", capture_runtime: dict | None = None) -> None:
    payload = json.dumps(capture_runtime) if capture_runtime is not None else ""

    def fake_get_state(key: str) -> str:
        if key == "capture_error_code":
            return capture_error_code
        if key == "capture_runtime":
            return payload
        return ""

    monkeypatch.setattr(
        "keypulse.store.repository.get_state",
        fake_get_state,
    )


def _healthy_watcher() -> dict:
    return {"running": True, "paused": False, "crashes": 0, "last_error": None, "gave_up": False}


# ---- accessibility_permission --------------------------------------------------


def test_accessibility_overrides_to_ok_when_capture_healthy(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    _mock_state(
        monkeypatch,
        capture_error_code="",
        capture_runtime={"watchers": {"window": _healthy_watcher(), "ax_text": _healthy_watcher()}},
    )
    state = AccessibilityPermissionCapability().monitor()
    assert state.ok
    assert state.code == "ok"
    assert "误报" in (state.detail or "")


def test_accessibility_no_override_when_capture_error_code_set(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    _mock_state(
        monkeypatch,
        capture_error_code="ax_denied",
        capture_runtime={"watchers": {"window": _healthy_watcher(), "ax_text": _healthy_watcher()}},
    )
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok
    assert state.code == "ax_denied"


def test_accessibility_no_override_when_watcher_gave_up(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    crashed = _healthy_watcher() | {"gave_up": True}
    _mock_state(
        monkeypatch,
        capture_error_code="",
        capture_runtime={"watchers": {"window": crashed, "ax_text": _healthy_watcher()}},
    )
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok
    assert state.code == "ax_denied"


def test_accessibility_no_override_when_watcher_has_last_error(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    erroring = _healthy_watcher() | {"last_error": "RuntimeError: boom"}
    _mock_state(
        monkeypatch,
        capture_error_code="",
        capture_runtime={"watchers": {"window": _healthy_watcher(), "ax_text": erroring}},
    )
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok
    assert state.code == "ax_denied"


def test_accessibility_no_override_when_runtime_missing(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    _mock_state(monkeypatch, capture_error_code="", capture_runtime=None)
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok
    assert state.code == "ax_denied"


def test_accessibility_no_override_when_runtime_malformed(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)

    def fake_get_state(key: str) -> str:
        return "not-json{" if key == "capture_runtime" else ""

    monkeypatch.setattr("keypulse.store.repository.get_state", fake_get_state)
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok
    assert state.code == "ax_denied"


def test_accessibility_no_override_when_required_watcher_missing(monkeypatch) -> None:
    """If a required watcher isn't in the runtime payload, treat as unhealthy."""
    _mock_probe_denied_ax(monkeypatch)
    _mock_state(
        monkeypatch,
        capture_error_code="",
        capture_runtime={"watchers": {"window": _healthy_watcher()}},  # ax_text missing
    )
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok


# ---- screen_recording_permission -----------------------------------------------


def test_screen_recording_overrides_to_ok_when_ocr_watcher_healthy(monkeypatch) -> None:
    _mock_probe_denied_screen(monkeypatch)
    _mock_state(
        monkeypatch,
        capture_runtime={"watchers": {"ocr": _healthy_watcher()}},
    )
    state = ScreenRecordingPermissionCapability().monitor()
    assert state.ok
    assert state.code == "ok"
    assert "误报" in (state.detail or "")


def test_screen_recording_no_override_when_ocr_has_last_error(monkeypatch) -> None:
    _mock_probe_denied_screen(monkeypatch)
    bad = _healthy_watcher() | {"last_error": "denied"}
    _mock_state(monkeypatch, capture_runtime={"watchers": {"ocr": bad}})
    state = ScreenRecordingPermissionCapability().monitor()
    assert not state.ok
    assert state.code == "screen_capture_denied"


def test_screen_recording_no_override_when_runtime_missing(monkeypatch) -> None:
    _mock_probe_denied_screen(monkeypatch)
    _mock_state(monkeypatch, capture_runtime=None)
    state = ScreenRecordingPermissionCapability().monitor()
    assert not state.ok
    assert state.code == "screen_capture_denied"
