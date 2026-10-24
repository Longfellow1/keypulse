"""Canonical LaunchAgent payloads written by Penpal installation commands."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from penpal.utils.env import get_env_compat
from penpal.utils.paths import get_data_dir


def resolve_penpal_executable() -> Path:
    override = (get_env_compat("PENPAL_EXECUTABLE", "") or "").strip()
    candidates = [override, shutil.which("penpal") or ""]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            resolved = shutil.which(str(path))
            path = Path(resolved) if resolved else path
        if path.exists():
            # Keep Homebrew's stable opt symlink instead of pinning a versioned Cellar path.
            return path.absolute()
    raise FileNotFoundError(
        "Could not find the installed `penpal` executable. Install with Homebrew first, "
        "or set PENPAL_EXECUTABLE=/absolute/path/to/penpal."
    )


def launchd_jobs(executable: Path | None = None) -> dict[str, dict[str, Any]]:
    executable = executable or resolve_penpal_executable()
    logs = get_data_dir() / "logs"
    env = {
        "PATH": ":".join(dict.fromkeys((
            str(executable.parent), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
            "/bin", "/usr/sbin", "/sbin",
        )))
    }
    return {
        "com.penpal.daemon": {
            "Label": "com.penpal.daemon",
            "ProgramArguments": [str(executable), "serve"],
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "ThrottleInterval": 10,
            "StandardOutPath": str(logs / "daemon.log"),
            "StandardErrorPath": str(logs / "daemon.err"),
            "EnvironmentVariables": env,
        },
        "com.penpal.healthcheck": {
            "Label": "com.penpal.healthcheck",
            "ProgramArguments": [str(executable), "healthcheck"],
            "StartInterval": 600,
            "RunAtLoad": True,
            "StandardOutPath": str(logs / "healthcheck.log"),
            "StandardErrorPath": str(logs / "healthcheck.err"),
            "EnvironmentVariables": env,
        },
    }
