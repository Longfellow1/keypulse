from __future__ import annotations

import pytest

import time
from pathlib import Path

from penpal.capabilities.base import CheckResult, HealthState, Signal
from penpal.capabilities.registry import CapabilityRegistry
from penpal.store.db import close, init_db
from penpal.store.repository import get_state


class _StaticCapability:
    level_when_failed = "err"
    label_when_failed = "采集异常"

    def __init__(self, name: str, state: HealthState) -> None:
        self.name = name
        self._state = state

    def precheck(self) -> CheckResult:
        return CheckResult(ok=True, code="ok")

    def monitor(self) -> HealthState:
        return self._state

    def diagnose(self, state: HealthState) -> Signal:
        return Signal(
            level="err",
            label=self.label_when_failed,
            hint=state.detail or state.code,
            action=None,
        )


def _init_test_db(tmp_path: Path) -> None:
    init_db(tmp_path / "penpal.db")


def _state(ok: bool, code: str, detail: str | None = None) -> HealthState:
    return HealthState(ok=ok, code=code, last_checked=time.time(), detail=detail)


@pytest.mark.skip(reason="OCR watcher disabled 2026-05-14")
def test_capture_fact_caps_contains_only_watcher_class() -> None:
    from penpal.app import _CAPTURE_FACT_CAPS

    probe_caps = {
        "accessibility_permission",
        "screen_recording_permission",
        "appkit_runtime",
    }

    assert probe_caps.isdisjoint(_CAPTURE_FACT_CAPS)
    assert _CAPTURE_FACT_CAPS == {
        "clipboard_watcher",
        "ax_text_watcher",
        "window_watcher",
        "browser_watcher",
        "ocr_watcher",
    }


def test_ax_denied_forces_capture_error_code_for_ax_dependent_watchers(tmp_path) -> None:
    from penpal.app import _run_capability_self_check

    _init_test_db(tmp_path)
    try:
        registry = CapabilityRegistry()
        registry.register(
            _StaticCapability("accessibility_permission", _state(False, "ax_denied"))
        )
        registry.register(_StaticCapability("ax_text_watcher", _state(True, "ok")))
        registry.register(_StaticCapability("keyboard_chunk_watcher", _state(True, "ok")))
        registry.register(_StaticCapability("clipboard_watcher", _state(True, "ok")))

        _run_capability_self_check(registry)

        assert get_state("capture_error_code") == "ax_denied"
    finally:
        close()


def test_watcher_failure_reflects_in_capture_error_code(tmp_path) -> None:
    from penpal.app import _run_capability_self_check

    _init_test_db(tmp_path)
    try:
        registry = CapabilityRegistry()
        registry.register(
            _StaticCapability("accessibility_permission", _state(True, "ok"))
        )
        registry.register(
            _StaticCapability("ax_text_watcher", _state(False, "ax_watcher_gave_up"))
        )

        _run_capability_self_check(registry)

        assert get_state("capture_error_code") == "ax_watcher_gave_up"
    finally:
        close()
