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

from keypulse.capabilities.base import Capability
from keypulse.capabilities.builtin._watcher_health import (
    WATCHER_HEALTH_SPECS,
    WatcherHealthCapability,
)
from keypulse.capabilities.builtin.accessibility_permission import AccessibilityPermissionCapability
from keypulse.capabilities.builtin.appkit_runtime import AppKitRuntimeCapability
from keypulse.capabilities.builtin.bundle_integrity import BundleIntegrityCapability
from keypulse.capabilities.builtin.browser_automation import BrowserAutomationCapability
from keypulse.capabilities.builtin.health_freshness import HealthFreshnessCapability
from keypulse.capabilities.builtin.llm_backend import LLMBackendCapability
from keypulse.capabilities.builtin.obsidian_sync_freshness import ObsidianSyncFreshnessCapability
from keypulse.capabilities.builtin.pause_state import PauseStateCapability
# from keypulse.capabilities.builtin.screen_recording_permission import ScreenRecordingPermissionCapability


# Each entry is a zero-arg factory returning a Capability. Factories instead
# of instances so consumers can inject app_path / config later if needed.
_DEFAULT_CAPABILITY_FACTORIES: list[Callable[..., Capability]] = [
    PauseStateCapability,
    AppKitRuntimeCapability,
    AccessibilityPermissionCapability,
    BrowserAutomationCapability,
    # === OCR watcher 已下线 2026-05-14 ===
    # 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
    # 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
    # 历史 raw_events 中 ocr_text_capture 数据保留可读
    # ScreenRecordingPermissionCapability,
    HealthFreshnessCapability,
    LLMBackendCapability,
    ObsidianSyncFreshnessCapability,
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
