from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Protocol

Level = Literal["ok", "warn", "err", "gray"]

CAPABILITY_ERROR_CODE = "capability_error"


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    code: str
    hint: Optional[str] = None
    action: Optional[str] = None


@dataclass(frozen=True)
class HealthState:
    ok: bool
    code: str
    last_checked: float
    detail: Optional[str] = None


@dataclass(frozen=True)
class Signal:
    level: Level
    label: str
    hint: Optional[str]
    action: Optional[str]


class Capability(Protocol):
    """Health-monitorable capability.

    `precheck()` runs at install/startup time (preflight, bundle integrity).
    `monitor()` runs on every supervisor tick (cheap, must not raise).
    `diagnose(state)` translates a failed state into a user-facing Signal.
    """

    name: str
    level_when_failed: Level
    label_when_failed: str

    def precheck(self) -> CheckResult: ...

    def monitor(self) -> HealthState: ...

    def diagnose(self, state: HealthState) -> Signal: ...
