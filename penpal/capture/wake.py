"""Sleep/wake detection for the daemon, without PyObjC.

The capture manager does not use PyObjC, so instead of NSWorkspaceDidWakeNotification we use a cheap
fallback: while the daemon sleeps in a short loop, the wall clock keeps running. A slice whose wall-clock
duration exceeds its expected duration by more than ``WAKE_JUMP_SEC`` means the Mac was asleep in between.
(The monotonic clock does not advance during sleep on macOS, so it cannot be used for this.)
"""

from __future__ import annotations

WAKE_JUMP_SEC = 60.0
# app_state key holding the ISO time of the last detected wake (read by penpal.health.capture_health).
LAST_WAKE_STATE_KEY = "last_wake_at"


def detect_wake(elapsed_wall_sec: float, expected_sec: float, *, jump_sec: float = WAKE_JUMP_SEC) -> bool:
    """True when a wall-clock interval ran longer than expected by more than ``jump_sec``."""
    return (elapsed_wall_sec - expected_sec) > jump_sec
