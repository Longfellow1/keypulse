from __future__ import annotations

import time
from pathlib import Path

from keypulse.capabilities.base import CheckResult, HealthState, Signal
from keypulse.capabilities.registry import CapabilityRegistry
from keypulse.hud import summary
from keypulse.store.db import close, init_db
from keypulse.store.repository import get_state


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
    init_db(tmp_path / "keypulse.db")


def _state(ok: bool, code: str, detail: str | None = None) -> HealthState:
    return HealthState(ok=ok, code=code, last_checked=time.time(), detail=detail)


def test_capture_fact_caps_contains_only_watcher_class() -> None:
    from keypulse.app import _CAPTURE_FACT_CAPS

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


def test_probe_failure_does_not_pollute_capture_error_code(tmp_path) -> None:
    from keypulse.app import _run_capability_self_check

    _init_test_db(tmp_path)
    try:
        registry = CapabilityRegistry()
        registry.register(
            _StaticCapability("accessibility_permission", _state(False, "ax_denied"))
        )
        registry.register(_StaticCapability("ax_text_watcher", _state(True, "ok")))
        registry.register(_StaticCapability("clipboard_watcher", _state(True, "ok")))

        _run_capability_self_check(registry)

        assert get_state("capture_error_code") == ""
    finally:
        close()


def test_watcher_failure_reflects_in_capture_error_code(tmp_path) -> None:
    from keypulse.app import _run_capability_self_check

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


def test_hud_summary_probe_only_failure_renders_as_hint(monkeypatch) -> None:
    monkeypatch.setattr(
        summary,
        "load_capability_states",
        lambda: {
            "accessibility_permission": HealthState(
                ok=False,
                code="ax_denied",
                last_checked=10.0,
                detail="denied",
            ),
            "ax_text_watcher": HealthState(ok=True, code="ok", last_checked=11.0),
        },
    )
    monkeypatch.setattr(summary, "read_health", lambda: {})
    monkeypatch.setattr(
        summary,
        "get_state",
        lambda key: {
            "capture_error_code": "",
            "llm_error_code": "",
        }.get(key, ""),
    )

    level, label, hint, action = summary.determine_service_status(
        capture_status="running",
    )

    assert level == "warn"
    assert label != "采集异常"
    assert "辅助功能" in hint
    assert action.startswith("open://")
