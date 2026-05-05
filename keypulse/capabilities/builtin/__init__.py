from __future__ import annotations

from pathlib import Path

from keypulse.capabilities.builtin.accessibility_permission import AccessibilityPermissionCapability
from keypulse.capabilities.builtin.appkit_runtime import AppKitRuntimeCapability
from keypulse.capabilities.builtin.bundle_integrity import BundleIntegrityCapability
from keypulse.capabilities.builtin.clipboard_watcher import ClipboardWatcherCapability
from keypulse.capabilities.builtin.health_freshness import HealthFreshnessCapability
from keypulse.capabilities.builtin.llm_backend import LLMBackendCapability
from keypulse.capabilities.builtin.pause_state import PauseStateCapability


def build_builtin_capabilities(*, app_path: Path | None = None) -> list:
    return [
        PauseStateCapability(),
        AppKitRuntimeCapability(),
        AccessibilityPermissionCapability(),
        ClipboardWatcherCapability(),
        HealthFreshnessCapability(),
        LLMBackendCapability(),
        BundleIntegrityCapability(app_path=app_path),
    ]


def register_builtin_capabilities(registry, *, app_path: Path | None = None) -> None:
    for cap in build_builtin_capabilities(app_path=app_path):
        registry.register(cap)
