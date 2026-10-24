"""Built-in capability registry.

Adding a new capability is one of two paths:
  1. **Watcher health** (an existing CaptureManager watcher's gave_up /
     heartbeat status): add one entry to WATCHER_HEALTH_SPECS in
     `_watcher_health.py`. No new file needed.
  2. **Novel capability** (anything else — permission, freshness, integrity):
     create a new module under this package implementing the Capability
     protocol, then add it to `_DEFAULT_CAPABILITY_FACTORIES` below.

Capabilities registered here are picked up by the supervisor (60s tick),
the HUD (read-only), the healthcheck CLI, and preflight.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from penpal.capabilities.base import Capability
from penpal.capabilities.builtin._watcher_health import (
    WATCHER_HEALTH_SPECS,
    WatcherHealthCapability,
)
from penpal.capabilities.builtin.accessibility_permission import AccessibilityPermissionCapability
from penpal.capabilities.builtin.appkit_runtime import AppKitRuntimeCapability
from penpal.capabilities.builtin.bundle_integrity import BundleIntegrityCapability
from penpal.capabilities.builtin.browser_automation import BrowserAutomationCapability
from penpal.capabilities.builtin.capture_storage import CaptureStorageCapability
from penpal.capabilities.builtin.pause_state import PauseStateCapability


# Each entry is a zero-arg factory returning a Capability. Factories instead
# of instances so consumers can inject app_path / config later if needed.
_DEFAULT_CAPABILITY_FACTORIES: list[Callable[..., Capability]] = [
    PauseStateCapability,
    AppKitRuntimeCapability,
    AccessibilityPermissionCapability,
    BrowserAutomationCapability,
    CaptureStorageCapability,
]


def build_builtin_capabilities(*, app_path: Path | None = None) -> list[Capability]:
    """Construct the default capability set.

    Order is preserved for deterministic iteration in tests, but does not
    affect runtime behavior — failure aggregation is by level priority.
    """
    caps: list[Capability] = [factory() for factory in _DEFAULT_CAPABILITY_FACTORIES]
    # Watcher health capabilities — one per spec.
    caps.extend(WatcherHealthCapability(spec) for spec in WATCHER_HEALTH_SPECS)
    # BundleIntegrity needs app_path injection; cannot be a zero-arg factory.
    caps.append(BundleIntegrityCapability(app_path=app_path))
    return caps


def register_builtin_capabilities(registry, *, app_path: Path | None = None) -> None:
    for cap in build_builtin_capabilities(app_path=app_path):
        registry.register(cap)
