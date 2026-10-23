"""Headless failure visibility (D33: the CLI is the minimal install, there is no UI to notice a stall).

Two consumers share this module and read only existing state (``app_state`` keys written by the
daemon: ``capture_runtime`` heartbeat snapshot, ``status``, and the supervisor's ``capabilities``):

- ``penpal doctor``: ``permission_report`` lists, per enabled source, which macOS permission it needs
  and whether it is granted, plus the binaries the permission must be granted to. Read-only: it uses
  the ``check`` side of ``PermissionSpec`` and never the ``request`` side, so no prompt can appear.
- the MCP server: ``current_capture_health`` returns ``{"code", "message"}`` when capture is broken,
  else ``None``, cached per process for ``CACHE_TTL_SEC``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from penpal import permissions
from penpal.capture.wake import LAST_WAKE_STATE_KEY
from penpal.permissions import PermissionStatus

# The daemon persists capture_runtime every >= 2 s while its flush loop is alive.
# 5 min tolerates a few missed persists without hiding a real stall for long.
HEARTBEAT_STALE_SEC = 300.0
# After a detected Mac wake the heartbeat is stale only because the machine slept; do not alarm for this long.
WAKE_GRACE_SEC = 120.0
CACHE_TTL_SEC = 30.0

# Watcher (= source) -> macOS permissions it needs. keyboard_chunk also depends on AX
# (see app._AX_DEPENDENT_FACT_CAPS). OCR is off (watcher commented out in capture/manager.py), so it
# needs no Screen Recording grant and is not listed here.
SOURCE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "keyboard_chunk": ("input_monitoring", "accessibility"),
    "ax_text": ("accessibility",),
}

# The daemon records its own permission state (app_state key below) on startup and on every
# capability self-check tick (60 s). Older than this, doctor assumes the daemon is not running.
DAEMON_PERMISSIONS_KEY = "daemon_permissions"
DAEMON_PERMISSION_MAX_AGE_SEC = 150.0

CODE_DAEMON_NOT_RUNNING = "daemon_not_running"
CODE_HEARTBEAT_STALE = "heartbeat_stale"
CODE_PAUSED = "capture_paused"
CODE_DISK_FULL = "disk_full"
CODE_STORAGE_ERROR = "storage_error"
CODE_PERMISSION_MISSING = "permission_missing"


# ── shared readers ──────────────────────────────────────────────────────────────

def _runtime_state() -> dict[str, Any]:
    from penpal.store.repository import get_state

    raw = get_state("capture_runtime")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _daemon_denied_permissions() -> set[str]:
    """Permissions the daemon itself reported denied (it runs under launchd, a different TCC identity
    from the process running doctor or the MCP server). Covers the capability probe for accessibility and
    the daemon's permission snapshot for every permission it checks (so a wake re-check is reflected)."""
    from penpal.capabilities.store import load_states

    denied: set[str] = set()
    state = load_states().get("accessibility_permission")
    if state is not None and not state.ok and state.code == "ax_denied":
        denied.add("accessibility")
    snapshot = daemon_permission_snapshot()
    for key, value in ((snapshot or {}).get("permissions") or {}).items():
        if value == PermissionStatus.DENIED.value:
            denied.add(key)
    return denied


def enabled_sources(cfg: Any, disabled: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Watchers that are on in config and not switched off with ``penpal sources disable``."""
    return [
        name
        for name in SOURCE_PERMISSIONS
        if bool(getattr(getattr(cfg, "watchers", None), name, False)) and name not in disabled
    ]


def required_permissions(cfg: Any, disabled: frozenset[str] | set[str] = frozenset()) -> dict[str, list[str]]:
    """permission key -> enabled sources that need it, in stable order."""
    needed: dict[str, list[str]] = {}
    for source in enabled_sources(cfg, disabled):
        for key in SOURCE_PERMISSIONS[source]:
            needed.setdefault(key, []).append(source)
    return needed


_PYTHON_BIN_RE = re.compile(r"^(?P<prefix>.*/Versions/[^/]+)/bin/python[0-9.]*$")


def grant_target(path: str) -> str:
    """The app macOS lists in Privacy panes for an interpreter path.

    Homebrew/python.org builds ship ``.../Versions/3.13/bin/python3.13`` next to
    ``.../Versions/3.13/Resources/Python.app``. The TCC grant belongs to that ``.app`` bundle, not to the
    ``bin`` symlink target, so return the bundle when it exists.
    """
    real = os.path.realpath(os.path.expanduser(path))
    match = _PYTHON_BIN_RE.match(real)
    if match:
        bundle = Path(match.group("prefix")) / "Resources" / "Python.app"
        if bundle.is_dir():
            return str(bundle)
    return real


def executables_to_grant(host_executable: Optional[str] = None) -> list[str]:
    """Programs a user must grant a permission to, most relevant first: the interpreter the daemon reported
    (its ``host_executable``), then this interpreter. Both are mapped to their ``.app`` bundle when they have one."""
    candidates = [host_executable or "", sys.executable]
    seen: set[str] = set()
    targets: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        target = grant_target(candidate)
        if target not in seen:
            seen.add(target)
            targets.append(target)
    return targets


# ── daemon self-report (written by the daemon, read by doctor) ─────────────────

def record_daemon_permissions() -> dict[str, Any]:
    """Daemon side: check each capture permission from inside the daemon process and persist the result.

    Read-only (``check`` only, never ``request``), so it cannot raise a prompt. The daemon runs under
    launchd with its own TCC identity; doctor runs in the user's terminal and cannot see that identity.
    """
    from penpal.store.repository import set_state

    results: dict[str, str] = {}
    for spec in permissions.PERMISSION_SPECS:
        try:
            results[spec.key] = spec.check().value
        except Exception:
            results[spec.key] = PermissionStatus.UNAVAILABLE.value
    snapshot = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "host_executable": os.path.realpath(sys.executable),
        "permissions": results,
    }
    set_state(DAEMON_PERMISSIONS_KEY, json.dumps(snapshot, ensure_ascii=False))
    return snapshot


def daemon_permission_snapshot(now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """Doctor side: the daemon's last self-reported permission state with ``age_sec`` added.

    ``None`` when nothing was recorded or the record is older than ``DAEMON_PERMISSION_MAX_AGE_SEC``
    (the daemon is then treated as not running).
    """
    from penpal.store.repository import get_state

    raw = get_state(DAEMON_PERMISSIONS_KEY)
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("permissions"), dict):
        return None
    checked = _seconds_since(value.get("checked_at"), now or datetime.now(timezone.utc))
    if checked is None or checked > DAEMON_PERMISSION_MAX_AGE_SEC:
        return None
    value["age_sec"] = int(checked)
    return value


def _within_wake_grace(now: datetime) -> bool:
    from penpal.store.repository import get_state

    since_wake = _seconds_since(get_state(LAST_WAKE_STATE_KEY), now)
    return since_wake is not None and since_wake < WAKE_GRACE_SEC


def _seconds_since(iso: Any, now: datetime) -> Optional[float]:
    if not isinstance(iso, str) or not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, (now - then).total_seconds())


# ── doctor ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PermissionFinding:
    key: str
    label: str
    sources: tuple[str, ...]
    granted: bool
    status: str  # "granted" | "missing" | "unavailable"
    settings_pane: str
    executables: tuple[str, ...]
    checked_by: str = "terminal"  # "daemon" (background service's own report) | "terminal" (fallback)
    age_sec: Optional[int] = None  # daemon report age; None for the terminal fallback


def permission_report(
    cfg: Any,
    *,
    disabled: frozenset[str] | set[str] | None = None,
    daemon_denied: set[str] | None = None,
    snapshot: Optional[Mapping[str, Any]] = None,
) -> list[PermissionFinding]:
    """Granted / missing per permission needed by an enabled source. Never prompts.

    Prefers the daemon's own recent report. Without one (daemon stopped, or never ran with this build),
    falls back to checking the current process, which is the user's terminal, not the background service.
    """
    if disabled is None:
        try:
            from penpal.capture.capture_control import disabled_sources

            disabled = disabled_sources()
        except Exception:
            disabled = frozenset()
    if daemon_denied is None:
        try:
            daemon_denied = _daemon_denied_permissions()
        except Exception:
            daemon_denied = set()
    if snapshot is None:
        try:
            snapshot = daemon_permission_snapshot()
        except Exception:
            snapshot = None
    daemon_perms: Mapping[str, Any] = (snapshot or {}).get("permissions") or {}
    exes = tuple(executables_to_grant((snapshot or {}).get("host_executable")))
    age = (snapshot or {}).get("age_sec")
    findings: list[PermissionFinding] = []
    for key, sources in required_permissions(cfg, disabled).items():
        spec = permissions.spec_by_key(key)
        if spec is None:
            continue
        if key in daemon_perms:
            current = _status_from_value(daemon_perms[key])
            checked_by, age_sec = "daemon", int(age) if isinstance(age, (int, float)) else None
        else:
            try:
                current = spec.check()
            except Exception:
                current = PermissionStatus.UNAVAILABLE
            checked_by, age_sec = "terminal", None
        granted = current == PermissionStatus.GRANTED and key not in daemon_denied
        status = "granted" if granted else ("unavailable" if current == PermissionStatus.UNAVAILABLE else "missing")
        findings.append(
            PermissionFinding(
                key=key,
                label=spec.label,
                sources=tuple(sources),
                granted=granted,
                status=status,
                settings_pane=spec.settings_pane,
                executables=exes,
                checked_by=checked_by,
                age_sec=age_sec,
            )
        )
    return findings


def _status_from_value(value: Any) -> PermissionStatus:
    try:
        return PermissionStatus(value)
    except ValueError:
        return PermissionStatus.UNAVAILABLE


# ── MCP capture_health ──────────────────────────────────────────────────────────

def _minutes_since(iso: Any, now: datetime) -> Optional[int]:
    if not isinstance(iso, str) or not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0, int((now - then).total_seconds() // 60))


def compute_capture_health(cfg: Any = None, *, now: Optional[datetime] = None) -> Optional[dict[str, str]]:
    """Uncached. ``None`` when healthy or when state cannot be read (unknown is not an alarm)."""
    from penpal.store.repository import get_state

    now = now or datetime.now(timezone.utc)
    runtime = _runtime_state()
    status = (get_state("status") or "").strip()
    minutes = _minutes_since(runtime.get("captured_at"), now)
    quiet = f"for {minutes} min" if minutes is not None else "yet"

    # 1. Daemon down / silent
    if not runtime or status == "stopped" or runtime.get("running") is False:
        return {
            "code": CODE_DAEMON_NOT_RUNNING,
            "message": f"Penpal has not recorded anything {quiet}: the daemon is stopped. Run `penpal start`.",
        }
    if minutes is not None and minutes * 60 >= HEARTBEAT_STALE_SEC and not _within_wake_grace(now):
        return {
            "code": CODE_HEARTBEAT_STALE,
            "message": (
                f"Penpal has not recorded anything for {minutes} min: the daemon has stopped responding "
                "(no heartbeat). Run `penpal doctor`, then `penpal start`."
            ),
        }
    # 2. Paused on purpose, but the user may have forgotten
    if status == "paused":
        return {
            "code": CODE_PAUSED,
            "message": "Penpal capture is paused, so new activity is not being recorded. Run `penpal resume` to continue.",
        }
    # 3. Writes failing
    storage = runtime.get("storage")
    if isinstance(storage, dict) and storage.get("status") == "error":
        if storage.get("reason") == "disk_full":
            return {
                "code": CODE_DISK_FULL,
                "message": "Penpal cannot save new records because the disk is full. Free up disk space; capture resumes by itself.",
            }
        return {
            "code": CODE_STORAGE_ERROR,
            "message": "Penpal cannot save new records (storage error). Run `penpal doctor`.",
        }
    # 4. A permission the daemon needs was lost
    try:
        denied = _daemon_denied_permissions()
    except Exception:
        denied = set()
    if denied:
        if cfg is None:
            from penpal.config import Config

            try:
                cfg = Config.load()
            except Exception:
                cfg = Config()
        try:
            from penpal.capture.capture_control import disabled_sources

            off = disabled_sources()
        except Exception:
            off = frozenset()
        affected = [(k, s) for k, s in required_permissions(cfg, off).items() if k in denied]
        if affected:
            names = ", ".join(sorted({src for _, srcs in affected for src in srcs}))
            labels = ", ".join(permissions.spec_by_key(k).label.split("（")[0] if permissions.spec_by_key(k) else k for k, _ in affected)
            return {
                "code": CODE_PERMISSION_MISSING,
                "message": (
                    f"Penpal lost a macOS permission ({labels}), so {names} capture is not recording. "
                    "Run `penpal doctor` to see which program to grant it to."
                ),
            }
    return None


_cache_lock = threading.Lock()
_cache: tuple[float, Optional[dict[str, str]]] | None = None


def current_capture_health() -> Optional[dict[str, str]]:
    """Cached (``CACHE_TTL_SEC``) per process; never raises."""
    global _cache
    now = time.monotonic()
    with _cache_lock:
        if _cache is not None and now < _cache[0]:
            return dict(_cache[1]) if _cache[1] else None
    try:
        value = compute_capture_health()
    except Exception:
        value = None
    with _cache_lock:
        _cache = (now + CACHE_TTL_SEC, value)
    return dict(value) if value else None


def reset_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None
