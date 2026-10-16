"""User presence detection via macOS IOKit IOHIDIdleTime.

Pure module. No DB. No threads. Callers poll is_user_present().
On non-darwin or missing IOKit: returns True (fail-open — don't
suppress capture on platforms we can't measure).
"""

import re
import subprocess
from typing import Optional


def idle_seconds() -> Optional[float]:
    """Return seconds since last HID event, or None if unavailable.

    Queries ioreg for IOHIDIdleTime (nanoseconds) and returns the minimum
    idle time across HID devices, converted to seconds. Returns None on
    any error (unsupported OS, ioreg failure, parse error).
    """
    try:
        result = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        output = result.stdout
        # Parse lines like: "HIDIdleTime" = 123456789 (nanoseconds)
        pattern = r'"HIDIdleTime"\s*=\s*(\d+)'
        matches = re.findall(pattern, output)

        if not matches:
            return None

        # Get minimum idle time across all devices (gives true idle)
        idle_ns = min(int(m) for m in matches)
        return idle_ns / 1_000_000_000.0

    except Exception:
        # Fail-open: None on any exception (no raise)
        return None


def is_user_present(*, threshold_seconds: float = 300.0) -> bool:
    """True if idle_seconds() < threshold. True when idle is None.

    Args:
        threshold_seconds: Maximum idle time to consider user present (default 5 min).

    Returns:
        True if user is present or measurement unavailable (fail-open).
        False only if idle_seconds() >= threshold.
    """
    idle = idle_seconds()
    if idle is None:
        return True  # Fail-open
    return idle < threshold_seconds
