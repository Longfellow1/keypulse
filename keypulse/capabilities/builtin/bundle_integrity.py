from __future__ import annotations

import plistlib
from pathlib import Path

from keypulse.capabilities.base import Capability, CheckResult, HealthState, Signal
from keypulse.capabilities.builtin._common import now_ts


_REQUIRED_PLIST_KEYS = (
    "CFBundleIdentifier",
    "NSAccessibilityUsageDescription",
    "NSAppleEventsUsageDescription",
    "NSScreenCaptureDescription",
)

_REQUIRED_FRAMEWORKS = (
    "AppKit",
    "Quartz",
    "Foundation",
    "ApplicationServices",
    "objc",
)


class BundleIntegrityCapability(Capability):
    name = "bundle_integrity"
    level_when_failed = "err"
    label_when_failed = "打包异常"

    def __init__(self, app_path: Path | None = None):
        self._app_path = app_path

    def with_app_path(self, app_path: Path | None) -> "BundleIntegrityCapability":
        return BundleIntegrityCapability(app_path=app_path)

    def _resolve_app_path(self) -> Path | None:
        if self._app_path is not None:
            return self._app_path
        return None

    def precheck(self) -> CheckResult:
        app_path = self._resolve_app_path()
        if app_path is None:
            return CheckResult(ok=True, code="skipped", hint="未指定 .app 路径，跳过打包完整性检查")

        errors = self._check_info_plist(app_path)
        errors.extend(self._check_frameworks(app_path))
        if errors:
            return CheckResult(ok=False, code="bundle_integrity_failed", hint="; ".join(errors))
        return CheckResult(ok=True, code="ok")

    def monitor(self) -> HealthState:
        return HealthState(ok=True, code="ok", last_checked=now_ts())

    def diagnose(self, state: HealthState) -> Signal:
        if state.ok:
            return Signal(level="ok", label="正常", hint="", action=None)
        return Signal(level="err", label=self.label_when_failed, hint=state.detail, action=None)

    def _check_info_plist(self, app_path: Path) -> list[str]:
        info = app_path / "Contents" / "Info.plist"
        if not info.exists():
            return [f"missing Info.plist at {info}"]
        payload = plistlib.loads(info.read_bytes())
        return [f"Info.plist missing key: {key}" for key in _REQUIRED_PLIST_KEYS if not payload.get(key)]

    def _find_resources_lib(self, app_path: Path) -> Path | None:
        base = app_path / "Contents" / "Resources" / "lib"
        if not base.exists():
            return None
        for child in sorted(base.iterdir()):
            if child.is_dir() and child.name.startswith("python"):
                return child
        return None

    def _check_frameworks(self, app_path: Path) -> list[str]:
        lib = self._find_resources_lib(app_path)
        if lib is None:
            return ["Contents/Resources/lib/python*/ not found — bundle layout unexpected"]

        site = lib / "python313.zip"
        candidates = [lib, lib / "site-packages"]
        if site.exists():
            candidates.append(site)

        errors: list[str] = []
        for name in _REQUIRED_FRAMEWORKS:
            if not self._framework_present(name, candidates):
                errors.append(
                    f"framework '{name}' not bundled (add to setup_app.py packages=[...])"
                )
        return errors

    def _framework_present(self, name: str, candidates: list[Path]) -> bool:
        for root in candidates:
            if not root.exists():
                continue
            if root.suffix == ".zip":
                import zipfile

                try:
                    with zipfile.ZipFile(root) as zf:
                        prefix = f"{name}/"
                        if any(n.startswith(prefix) or n == f"{name}.py" for n in zf.namelist()):
                            return True
                except zipfile.BadZipFile:
                    continue
                continue
            if (root / name).is_dir():
                return True
            if (root / f"{name}.py").is_file():
                return True
        return False
