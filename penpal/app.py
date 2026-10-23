from __future__ import annotations
import os
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Any

from penpal.capabilities.base import HealthState
from penpal.capabilities.registry import CapabilityRegistry, get_default_registry
from penpal.capabilities.store import save_states as save_capability_states
from penpal.capture.browser_automation_state import clear_browser_automation_denied_browsers_on_startup
from penpal.config import Config
from penpal.utils.lock import SingleInstanceLock
from penpal.utils.logging import setup_logging, get_logger
from penpal.store.db import init_db
from penpal.sources.scheduler import SourcesScheduler

logger = get_logger("app")
_CAPTURE_FACT_CAPS = {
    "clipboard_watcher",
    "ax_text_watcher",
    "window_watcher",
    "browser_watcher",
    # === OCR watcher 已下线 2026-05-14 ===
    # 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
    # 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
    # 历史 raw_events 中 ocr_text_capture 数据保留可读
    # "ocr_watcher",
}
_CAPTURE_PROBE_CAPS = {
    "accessibility_permission",
    "browser_automation",
    # === OCR watcher 已下线 2026-05-14 ===
    # 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
    # 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
    # 历史 raw_events 中 ocr_text_capture 数据保留可读
    # "screen_recording_permission",
    "appkit_runtime",
}
_COMPAT_CAPTURE_CAPS = _CAPTURE_FACT_CAPS
_AX_DEPENDENT_FACT_CAPS = {
    "ax_text_watcher",
    "keyboard_chunk_watcher",
}


def daemonize(pid_path: Path):
    """
    Standard Unix double-fork daemonization.
    After this call, the current process IS the daemon (new PID written to pid_path).
    The calling process (parent) exits immediately after first fork.
    """
    # First fork
    pid = os.fork()
    if pid > 0:
        # Parent: wait briefly then exit
        sys.exit(0)

    os.setsid()
    os.umask(0o022)

    # Second fork
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # Redirect stdio to /dev/null
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, sys.stdin.fileno())
    os.dup2(devnull, sys.stdout.fileno())
    os.dup2(devnull, sys.stderr.fileno())
    os.close(devnull)

    # PID is written by the lock layer inside run().


def _run_capability_self_check(registry: CapabilityRegistry) -> None:
    """Probe all capabilities and persist results to the state repo.

    The state repo is the runtime source of truth for capability health.
    health.json is written by the healthcheck CLI on its own cadence and
    embeds a snapshot of capability state at that time.
    """
    from penpal.store.repository import set_state

    states = registry.monitor_all()
    states = _apply_ax_denied_fact_overrides(states)
    save_capability_states(states)

    # Record what this process sees for its own permissions, so `penpal doctor` can report the
    # background service instead of the terminal it runs in. Never let this break the self-check.
    try:
        from penpal.health.capture_health import record_daemon_permissions

        record_daemon_permissions()
    except Exception as exc:
        logger.warning(f"daemon permission self-report failed: {exc}")

    capture_failure = registry.select_failure(states, names=_CAPTURE_FACT_CAPS)
    set_state("capture_error_code", "" if capture_failure is None else capture_failure.state.code)


def _apply_ax_denied_fact_overrides(states: dict[str, HealthState]) -> dict[str, HealthState]:
    """Force AX-dependent watcher facts false when accessibility is denied."""
    accessibility_state = states.get("accessibility_permission")
    if accessibility_state is None or accessibility_state.ok or accessibility_state.code != "ax_denied":
        return states

    now = time.time()
    overridden = dict(states)
    for capability_name in _AX_DEPENDENT_FACT_CAPS:
        if capability_name not in overridden:
            continue
        overridden[capability_name] = HealthState(
            ok=False,
            code="ax_denied",
            last_checked=now,
            detail="forced by accessibility_permission=ax_denied",
        )
    return overridden


def _on_mac_wake(registry: CapabilityRegistry, scheduler: SourcesScheduler) -> None:
    """After the Mac wakes: the same read-only self-check the supervisor runs (updates the stored
    permission state that doctor and MCP capture_health read), then a rescan of every source."""
    _run_capability_self_check(registry)
    scheduler.rescan_all(reason="mac_wake")


_self_check_supervisor_started = False


def _spawn_self_check_supervisor(interval_sec: float = 60.0, registry: CapabilityRegistry | None = None) -> None:
    """Re-run capability monitor every interval_sec and persist capability states."""
    global _self_check_supervisor_started
    if _self_check_supervisor_started:
        return
    _self_check_supervisor_started = True
    cap_registry = registry or get_default_registry()

    def _loop() -> None:
        while True:
            time.sleep(interval_sec)
            try:
                _run_capability_self_check(cap_registry)
            except Exception as exc:
                logger.error(f"self-check supervisor tick failed: {exc}")

    thread = threading.Thread(target=_loop, daemon=True, name="self-check-supervisor")
    thread.start()
    logger.info(f"self-check supervisor running (interval={interval_sec}s)")


def run(config: Optional[Config] = None):
    """
    Main daemon execution: starts CaptureManager, blocks until SIGTERM/SIGINT.
    Can run in the foreground or after daemonize().
    """
    if config is None:
        config = Config.load()

    setup_logging(config.log_path_expanded)
    logger.info("Penpal daemon starting")

    lock = SingleInstanceLock()
    if not lock.acquire():
        logger.error(f"Another Penpal instance is already running (PID {lock.get_pid()})")
        sys.exit(1)

    try:
        from penpal.permissions import trigger_required_prompts
        prompt_results = trigger_required_prompts()
        logger.info("permission prompt results: %s", prompt_results)
    except Exception as exc:
        logger.warning("permission prompt trigger failed: %s", exc)

    init_db(config.db_path_expanded)
    cleared_denied = clear_browser_automation_denied_browsers_on_startup()
    logger.info(
        "daemon startup reset browser automation denied cache (cleared=%d, browsers=%s)",
        len(cleared_denied),
        ", ".join(cleared_denied) if cleared_denied else "-",
    )
    registry = get_default_registry()
    _run_capability_self_check(registry)
    _spawn_self_check_supervisor(registry=registry)
    scheduler_cfg = config.sources.scheduler
    scheduler = SourcesScheduler(
        enabled=scheduler_cfg.enabled,
        poll_interval_sec=scheduler_cfg.poll_interval_sec,
        debounce_sec=scheduler_cfg.debounce_sec,
    )

    from penpal.capture.manager import CaptureManager

    manager = CaptureManager(config)
    manager.set_wake_handler(lambda: _on_mac_wake(registry, scheduler))

    def _shutdown(signum, frame):
        logger.info(f"Signal {signum} received, shutting down")
        try:
            scheduler.stop()
            manager.stop()
        except Exception as e:
            logger.error(f"Error during stop: {e}")
        finally:
            lock.release()
            sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        manager.start()
        logger.info("Penpal daemon running")

        scheduler.start()

        while True:
            time.sleep(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        try:
            scheduler.stop()
        except Exception as stop_exc:
            logger.error(f"Error stopping sources scheduler after fatal error: {stop_exc}")
        try:
            manager.stop()
        except Exception as stop_exc:
            logger.error(f"Error stopping capture manager after fatal error: {stop_exc}")
        lock.release()
        sys.exit(1)

    finally:
        lock.release()


def start_daemon(config: Optional[Config] = None):
    """
    Fork to background and start daemon.
    Returns immediately in the original (parent) process.
    The child becomes the daemon.
    """
    if config is None:
        config = Config.load()

    from penpal.utils.paths import get_pid_path
    pid_path = get_pid_path()

    # Double-fork: after this, parent exits, child continues as daemon
    daemonize(pid_path)

    # --- Only daemon process reaches here ---
    run(config)
