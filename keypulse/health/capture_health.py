"""Headless failure visibility (D33: the CLI is the minimal install, there is no UI to notice a stall).

Two consumers share this module and read only existing state (``app_state`` keys written by the
daemon: ``capture_runtime`` heartbeat snapshot, ``status``, and the supervisor's ``capabilities``):

- ``keypulse doctor``: ``permission_report`` lists, per enabled source, which macOS permission it needs
  and whether it is granted, plus the binaries the permission must be granted to. Read-only: it uses
  the ``check`` side of ``PermissionSpec`` and never the ``request`` side, so no prompt can appear.
- the MCP server: ``current_capture_health`` returns ``{"code", "message"}`` when capture is broken,
  else ``None``, cached per process for ``CACHE_TTL_SEC``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from keypulse import permissions
from keypulse.permissions import PermissionStatus

# The daemon persists capture_runtime every >= 2 s while its flush loop is alive.
# 5 min tolerates a few missed persists without hiding a real stall for long.
HEARTBEAT_STALE_SEC = 300.0
CACHE_TTL_SEC = 30.0

# Watcher (= source) -> macOS permissions it needs. keyboard_chunk also depends on AX
# (see app._AX_DEPENDENT_FACT_CAPS).
SOURCE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "keyboard_chunk": ("input_monitoring", "accessibility"),
    "ax_text": ("accessibility",),
    "ocr": ("screen_recording",),
}

CODE_DAEMON_NOT_RUNNING = "daemon_not_running"
CODE_HEARTBEAT_STALE = "heartbeat_stale"
CODE_PAUSED = "capture_paused"
CODE_DISK_FULL = "disk_full"
CODE_STORAGE_ERROR = "storage_error"
CODE_PERMISSION_MISSING = "permission_missing"


# ── shared readers ──────────────────────────────────────────────────────────────

def _runtime_state() -> dict[str, Any]:
    from keypulse.store.repository import get_state

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
    from the process running doctor or the MCP server)."""
    from keypulse.capabilities.store import load_states

    denied: set[str] = set()
    state = load_states().get("accessibility_permission")
    if state is not None and not state.ok and state.code == "ax_denied":
        denied.add("accessibility")
    return denied


def enabled_sources(cfg: Any, disabled: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Watchers that are on in config and not switched off with ``keypulse sources disable``."""
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


def executables_to_grant(runtime: Optional[Mapping[str, Any]] = None) -> list[str]:
    """Binaries a user must grant a permission to, most relevant first: the interpreter the running daemon
    reported (``host_executable``), the ``keypulse`` console script launchd starts, and this interpreter."""
    paths: list[str] = []
    host = (runtime or {}).get("host_executable")
    if isinstance(host, str) and host:
        paths.append(host)
    override = os.environ.get("KEYPULSE_EXECUTABLE", "").strip()
    script = override or shutil.which("keypulse") or ""
    if script:
        paths.append(os.path.realpath(os.path.expanduser(script)))
    paths.append(os.path.realpath(sys.executable))
    seen: set[str] = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


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


def permission_report(
    cfg: Any,
    *,
    disabled: frozenset[str] | set[str] | None = None,
    daemon_denied: set[str] | None = None,
    runtime: Optional[Mapping[str, Any]] = None,
) -> list[PermissionFinding]:
    """Granted / missing per permission needed by an enabled source. Never prompts."""
    if disabled is None:
        try:
            from keypulse.capture.capture_control import disabled_sources

            disabled = disabled_sources()
        except Exception:
            disabled = frozenset()
    if daemon_denied is None:
        try:
            daemon_denied = _daemon_denied_permissions()
        except Exception:
            daemon_denied = set()
    exes = tuple(executables_to_grant(runtime))
    findings: list[PermissionFinding] = []
    for key, sources in required_permissions(cfg, disabled).items():
        spec = permissions.spec_by_key(key)
        if spec is None:
            continue
        try:
            current = spec.check()
        except Exception:
            current = PermissionStatus.UNAVAILABLE
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
            )
        )
    return findings


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
    from keypulse.store.repository import get_state

    now = now or datetime.now(timezone.utc)
    runtime = _runtime_state()
    status = (get_state("status") or "").strip()
    minutes = _minutes_since(runtime.get("captured_at"), now)
    quiet = f"for {minutes} min" if minutes is not None else "yet"

    # 1. Daemon down / silent
    if not runtime or status == "stopped" or runtime.get("running") is False:
        return {
            "code": CODE_DAEMON_NOT_RUNNING,
            "message": f"KeyPulse has not recorded anything {quiet}: the daemon is stopped. Run `keypulse start`.",
        }
    if minutes is not None and minutes * 60 >= HEARTBEAT_STALE_SEC:
        return {
            "code": CODE_HEARTBEAT_STALE,
            "message": (
                f"KeyPulse has not recorded anything for {minutes} min: the daemon has stopped responding "
                "(no heartbeat). Run `keypulse doctor`, then `keypulse start`."
            ),
        }
    # 2. Paused on purpose, but the user may have forgotten
    if status == "paused":
        return {
            "code": CODE_PAUSED,
            "message": "KeyPulse capture is paused, so new activity is not being recorded. Run `keypulse resume` to continue.",
        }
    # 3. Writes failing
    storage = runtime.get("storage")
    if isinstance(storage, dict) and storage.get("status") == "error":
        if storage.get("reason") == "disk_full":
            return {
                "code": CODE_DISK_FULL,
                "message": "KeyPulse cannot save new records because the disk is full. Free up disk space; capture resumes by itself.",
            }
        return {
            "code": CODE_STORAGE_ERROR,
            "message": "KeyPulse cannot save new records (storage error). Run `keypulse doctor`.",
        }
    # 4. A permission the daemon needs was lost
    try:
        denied = _daemon_denied_permissions()
    except Exception:
        denied = set()
    if denied:
        if cfg is None:
            from keypulse.config import Config

            try:
                cfg = Config.load()
            except Exception:
                cfg = Config()
        try:
            from keypulse.capture.capture_control import disabled_sources

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
                    f"KeyPulse lost a macOS permission ({labels}), so {names} capture is not recording. "
                    "Run `keypulse doctor` to see which program to grant it to."
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
