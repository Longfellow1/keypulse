"""Permission preflight false positives defer to shared daemon runtime evidence."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from penpal.capabilities.builtin.accessibility_permission import AccessibilityPermissionCapability
from penpal.capabilities.builtin.screen_recording_permission import ScreenRecordingPermissionCapability
from penpal.runtime import RuntimeState, RuntimeStatus
from penpal.runtime.record import Component, Progress

NOW = datetime.now(timezone.utc)


def _mock_probe_denied_ax(monkeypatch) -> None:
    fake = types.ModuleType("ApplicationServices")
    fake.AXIsProcessTrusted = lambda: False  # type: ignore
    monkeypatch.setitem(sys.modules, "ApplicationServices", fake)


def _mock_probe_denied_screen(monkeypatch) -> None:
    fake = types.ModuleType("Quartz")
    fake.CGPreflightScreenCaptureAccess = lambda: False  # type: ignore
    monkeypatch.setitem(sys.modules, "Quartz", fake)


def _mock_runtime(monkeypatch, components: dict[str, Component], *, emits: dict[str, str] | None = None,
                  state: RuntimeState = RuntimeState.RUNNING) -> None:
    progress = {f"emit.{name}": Progress(1, stamp) for name, stamp in (emits or {}).items()}
    status = RuntimeStatus(state, components=components, progress=progress)
    monkeypatch.setattr("penpal.capabilities.builtin._common.evaluate", lambda: status)


def _healthy_capture() -> dict[str, Component]:
    return {"window": Component("running"), "ax_text": Component("running")}


def test_accessibility_overrides_to_ok_when_capture_healthy(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    _mock_runtime(monkeypatch, _healthy_capture(), emits={"ax_text": NOW.isoformat()})
    state = AccessibilityPermissionCapability().monitor()
    assert state.ok and state.code == "ok"
    assert "误报" in (state.detail or "")


def test_accessibility_no_override_when_core_loop_is_hung(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    _mock_runtime(monkeypatch, _healthy_capture(), emits={"ax_text": NOW.isoformat()}, state=RuntimeState.HUNG)
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok and state.code == "ax_denied"


def test_accessibility_no_override_when_watcher_gave_up(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    _mock_runtime(monkeypatch, {"window": Component("running"), "ax_text": Component("gave_up")},
                  emits={"ax_text": NOW.isoformat()})
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok and state.code == "ax_denied"


def test_accessibility_no_override_when_emit_is_stale(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    old = (NOW - timedelta(hours=3)).isoformat()
    _mock_runtime(monkeypatch, _healthy_capture(), emits={"ax_text": old})
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok and state.code == "ax_denied"


def test_accessibility_no_override_when_watcher_never_emitted(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    _mock_runtime(monkeypatch, _healthy_capture())
    state = AccessibilityPermissionCapability().monitor()
    assert not state.ok and state.code == "ax_denied"


def test_accessibility_no_override_when_runtime_or_required_watcher_is_missing(monkeypatch) -> None:
    _mock_probe_denied_ax(monkeypatch)
    _mock_runtime(monkeypatch, {}, state=RuntimeState.NOT_RUNNING)
    assert AccessibilityPermissionCapability().monitor().code == "ax_denied"
    _mock_runtime(monkeypatch, {"window": Component("running")})
    assert AccessibilityPermissionCapability().monitor().code == "ax_denied"


@pytest.mark.skip(reason="OCR watcher disabled 2026-05-14")
def test_screen_recording_overrides_to_ok_when_ocr_watcher_healthy(monkeypatch) -> None:
    _mock_probe_denied_screen(monkeypatch)
    _mock_runtime(monkeypatch, {"ocr": Component("running")}, emits={"ocr": NOW.isoformat()})
    state = ScreenRecordingPermissionCapability().monitor()
    assert state.ok and state.code == "ok"


@pytest.mark.skip(reason="OCR watcher disabled 2026-05-14")
def test_screen_recording_no_override_when_ocr_is_not_healthy(monkeypatch) -> None:
    _mock_probe_denied_screen(monkeypatch)
    _mock_runtime(monkeypatch, {"ocr": Component("running")})
    state = ScreenRecordingPermissionCapability().monitor()
    assert not state.ok and state.code == "screen_capture_denied"
