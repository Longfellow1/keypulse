from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from keypulse.capabilities.base import (
    CAPABILITY_ERROR_CODE,
    Capability,
    CheckResult,
    HealthState,
    Signal,
)
from keypulse.capabilities.builtin import register_builtin_capabilities

_LEVEL_PRIORITY = {"ok": 0, "gray": 1, "warn": 2, "err": 3}
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FailureSelection:
    name: str
    state: HealthState
    signal: Signal


class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}
        self._lock = threading.Lock()

    def register(self, cap: Capability) -> None:
        with self._lock:
            self._caps[cap.name] = cap

    def precheck_all(self) -> list[tuple[str, CheckResult]]:
        with self._lock:
            caps = list(self._caps.items())
        results: list[tuple[str, CheckResult]] = []
        for name, cap in caps:
            try:
                results.append((name, cap.precheck()))
            except Exception as exc:
                _LOGGER.exception("capability %s precheck raised", name)
                results.append((
                    name,
                    CheckResult(ok=False, code=CAPABILITY_ERROR_CODE, hint=str(exc)),
                ))
        return results

    def monitor_all(self) -> dict[str, HealthState]:
        """Probe every capability. A failing capability cannot block others."""
        with self._lock:
            caps = list(self._caps.items())
        states: dict[str, HealthState] = {}
        for name, cap in caps:
            try:
                states[name] = cap.monitor()
            except Exception as exc:
                _LOGGER.exception("capability %s monitor raised", name)
                states[name] = HealthState(
                    ok=False,
                    code=CAPABILITY_ERROR_CODE,
                    last_checked=time.time(),
                    detail=str(exc),
                )
        return states

    def select_failure(self, states: dict[str, HealthState], names: set[str] | None = None) -> FailureSelection | None:
        candidates: list[FailureSelection] = []
        for name, state in states.items():
            if names is not None and name not in names:
                continue
            if state.ok:
                continue
            cap = self._caps.get(name)
            if cap is None:
                signal = Signal(level="warn", label="异常", hint=state.detail or state.code, action=None)
            else:
                signal = cap.diagnose(state)
            candidates.append(FailureSelection(name=name, state=state, signal=signal))

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                _LEVEL_PRIORITY.get(item.signal.level, 0),
                item.state.last_checked,
            ),
            reverse=True,
        )
        return candidates[0]

    def aggregate_signal(self, states: dict[str, HealthState]) -> Signal:
        chosen = self.select_failure(states)
        if chosen is None:
            return Signal(level="ok", label="正常", hint="", action=None)
        return chosen.signal

    def find_capability_for_code(self, code: str, *, names: set[str] | None = None) -> Capability | None:
        for cap in self._caps.values():
            if names is not None and cap.name not in names:
                continue
            error_codes = getattr(cap, "error_codes", None)
            if isinstance(error_codes, set) and code in error_codes:
                return cap
        return None

    def get(self, name: str) -> Capability:
        if name not in self._caps:
            raise KeyError(f"capability not found: {name}")
        return self._caps[name]

    def __iter__(self) -> Iterator[Capability]:
        return iter(self._caps.values())


_default_registry: CapabilityRegistry | None = None


def build_default_registry(*, app_path: Path | None = None) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    register_builtin_capabilities(registry, app_path=app_path)
    return registry


def get_default_registry() -> CapabilityRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry


def set_default_registry_for_tests(registry: Optional[CapabilityRegistry]) -> None:
    global _default_registry
    _default_registry = registry
