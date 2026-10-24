"""Optional desktop App install (D33): `penpal app install` / `penpal app uninstall`.

The App is a thin launcher bundle, not a second copy of Penpal: its only executable execs the installed
`penpal hud`. So the CLI stays the one source of truth, the daemon keeps every macOS permission (TCC),
and the bundle declares none (no NS*UsageDescription keys): it needs no permission of its own.

Every location is a parameter (apps dir, LaunchAgents dir, subprocess runner) so tests run entirely in tmp dirs.
"""
from __future__ import annotations

import plistlib
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from penpal.runtime.control import load_job, unload_job

from penpal.utils.paths import get_apps_dir, get_launchd_agents_dir

APP_NAME = "Penpal.app"
BUNDLE_ID = "com.penpal.app"
EXECUTABLE_NAME = "Penpal"
HUD_LABEL = "com.penpal.hud"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class AppBundleError(Exception):
    """A readable, user-facing failure (the CLI prints it as is)."""


@dataclass
class AppResult:
    bundle: Path
    actions: list[str] = field(default_factory=list)


def default_apps_dir() -> Path:
    return get_apps_dir()


def default_agents_dir() -> Path:
    return get_launchd_agents_dir()


def _run(cmd: list[str], **kwargs) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(cmd, check=False, capture_output=True, text=True, **kwargs)


def _is_ours(bundle: Path) -> bool:
    try:
        info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    return info.get("CFBundleIdentifier") == BUNDLE_ID


def info_plist(version: str) -> dict:
    return {
        "CFBundleName": "Penpal",
        "CFBundleDisplayName": "Penpal",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": EXECUTABLE_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": version,
        "CFBundleShortVersionString": version,
        "LSUIElement": True,  # menu bar only, no Dock icon
        "NSHighResolutionCapable": True,
    }


def launch_script(executable: Path) -> str:
    return f"#!/bin/sh\nexec {shlex.quote(str(executable))} hud\n"


def hud_agent_plist(bundle: Path) -> dict:
    return {
        "Label": HUD_LABEL,
        "ProgramArguments": [str(bundle / "Contents" / "MacOS" / EXECUTABLE_NAME)],
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
    }


def install_app(
    executable: Path,
    *,
    version: str,
    apps_dir: Optional[Path] = None,
    agents_dir: Optional[Path] = None,
    login: bool = False,
    run: Optional[Runner] = None,
) -> AppResult:
    """Build `<apps_dir>/Penpal.app`, ad-hoc sign it, and (with ``login``) register the HUD launch agent."""
    run = run or _run
    bundle = (apps_dir or default_apps_dir()) / APP_NAME
    result = AppResult(bundle)
    if bundle.exists():
        if not _is_ours(bundle):
            raise AppBundleError(f"{bundle} exists and is not a Penpal app; move it away or use --dir.")
        shutil.rmtree(bundle)  # rebuild from scratch so a stale signature or script cannot linger
        result.actions.append("replaced existing app")

    macos = bundle / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info_plist(version)))
    script = macos / EXECUTABLE_NAME
    script.write_text(launch_script(executable), encoding="utf-8")
    script.chmod(0o755)
    result.actions.append(f"built {bundle}")

    signed = run(["codesign", "--force", "-s", "-", str(bundle)])
    if signed.returncode != 0:
        shutil.rmtree(bundle, ignore_errors=True)
        raise AppBundleError("codesign failed; the app was not installed. " + (signed.stderr or "").strip()[:200])
    result.actions.append("signed ad hoc")

    if login:
        agents = agents_dir or default_agents_dir()
        agents.mkdir(parents=True, exist_ok=True)
        plist = agents / f"{HUD_LABEL}.plist"
        plist.write_bytes(plistlib.dumps(hud_agent_plist(bundle), sort_keys=False))
        _bootout(plist, run)
        if not _bootstrap(plist, run):
            raise AppBundleError(f"wrote {plist} but could not register the login item; retry with `penpal app install --login`.")
        result.actions.append(f"login item registered ({HUD_LABEL})")
    return result


def uninstall_app(
    *,
    apps_dir: Optional[Path] = None,
    agents_dir: Optional[Path] = None,
    run: Optional[Runner] = None,
) -> AppResult:
    """Remove the login agent and the app bundle. Only a bundle with our identifier is deleted."""
    run = run or _run
    bundle = (apps_dir or default_apps_dir()) / APP_NAME
    result = AppResult(bundle)
    plist = (agents_dir or default_agents_dir()) / f"{HUD_LABEL}.plist"
    if plist.exists():
        _bootout(plist, run)
        plist.unlink()
        result.actions.append(f"removed {plist}")
    if bundle.exists():
        if not _is_ours(bundle):
            raise AppBundleError(f"{bundle} is not a Penpal app; leaving it alone.")
        shutil.rmtree(bundle)
        result.actions.append(f"removed {bundle}")
    return result


def _bootout(plist: Path, run: Runner) -> None:
    unload_job(plist.stem, plist, runner=lambda command, **_kwargs: run(command))


def _bootstrap(plist: Path, run: Runner) -> bool:
    return load_job(plist.stem, plist, runner=lambda command, **_kwargs: run(command))
