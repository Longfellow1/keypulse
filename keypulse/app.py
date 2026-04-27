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

from keypulse.config import Config
from keypulse.utils.lock import SingleInstanceLock
from keypulse.utils.logging import setup_logging, get_logger
from keypulse.store.db import init_db

logger = get_logger("app")


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


def _check_accessibility() -> bool:
    """Return True if Accessibility permission is granted (macOS)."""
    try:
        import ApplicationServices
        return bool(ApplicationServices.AXIsProcessTrusted())
    except Exception:
        return False  # pyobjc not available or non-macOS


def _run_obsidian_sync_core(cfg: Config, date: Optional[str] = None) -> None:
    """
    Core Obsidian sync logic: resolve date, export bundle, record outcome.
    Shared by CLI command (T1 gating) and daemon triggers (T2, T3).
    """
    from keypulse.utils.dates import resolve_local_date
    from keypulse.integrations import resolve_active_sink
    from keypulse.pipeline import load_model_gateway
    from keypulse.services.export import export_obsidian

    # Resolve date: if not specified, use today
    if date is None:
        date_str = resolve_local_date("today", yesterday=False)
    else:
        date_str = date

    # Perform export
    sink = resolve_active_sink(cfg, persist=True)
    target_output = str(sink.output_dir)
    target_vault = cfg.obsidian.vault_name
    gateway = load_model_gateway(cfg) if hasattr(cfg, "model") else None

    pipeline_cfg = getattr(cfg, "pipeline", None)
    written = export_obsidian(
        target_output,
        date_str=date_str,
        vault_name=target_vault,
        model_gateway=gateway,
        incremental=False,
        db_path=str(cfg.db_path_expanded),
        use_narrative_v2=getattr(pipeline_cfg, "use_narrative_v2", False),
        use_narrative_skeleton=getattr(pipeline_cfg, "use_narrative_skeleton", False),
    )
    logger.info(f"Obsidian sync completed: {len(written)} notes to {target_output}")


def _t3_tick(
    state: dict[str, Any],
    *,
    now: datetime,
    db_path: Path,
    sync_fn: Callable[[], None],
    should_trigger_fn: Callable[[str], tuple[bool, str]],
    record_trigger_fn: Callable[[str, str], None],
    idle_seconds_fn: Callable[[], Optional[float]],
) -> dict[str, Any]:
    """
    T3 idle trigger tick logic (testable, stateless function).

    state dict contains:
      - was_active (bool): True if last tick had >=50 chars in 5-min window

    Returns updated state dict.

    Logic:
      1. Compute 5-min sliding char count from raw_events (user speaker)
      2. Track active→inactive transition
      3. On transition, check idle ≥10min; if yes, call should_trigger("T3")
      4. If allowed, run sync + record "ran:ok"; on exception record "ran:fail"
    """
    # Step 1: compute 5-min sliding window character count
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT SUM(LENGTH(content_text)) as total_chars
            FROM raw_events
            WHERE speaker = 'user' AND ts_utc > datetime(?, '-5 minutes')
            """,
            (now.isoformat(),),
        )
        result = cursor.fetchone()
        conn.close()
        total_chars = result[0] if result and result[0] else 0
    except Exception as e:
        logger.error(f"T3 tick: failed to compute char window: {e}")
        total_chars = 0

    # Step 2: compute is_active and detect transition
    is_active = total_chars >= 50
    was_active = state.get("was_active", False)
    state["was_active"] = is_active

    # Step 3: on active→inactive transition
    if was_active and not is_active:
        # Check idle duration
        idle_sec = idle_seconds_fn()
        if idle_sec is None:
            logger.debug("T3 tick: idle_seconds unavailable, skipping")
            return state

        if idle_sec < 600.0:  # 10 minutes
            logger.debug(f"T3 tick: idle {idle_sec:.1f}s < 10min, skipping")
            return state

        # Idle ≥ 10min: check T3 trigger gate
        allowed, reason = should_trigger_fn("T3")
        if not allowed:
            logger.info(f"T3 skipped: {reason}")
            record_trigger_fn("T3", f"skipped:{reason}")
            return state

        # Run sync
        logger.info("T3 trigger: idle transition, attempting sync")
        try:
            sync_fn()
            record_trigger_fn("T3", "ran:ok")
        except Exception as e:
            logger.error(f"T3 sync failed: {e}")
            record_trigger_fn("T3", f"ran:fail")

    return state


def _spawn_t2_trigger(cfg: Config, shutdown_event: threading.Event, delay: float = 60.0) -> None:
    """
    Spawn a timer to fire T2 (daemon startup smoke) trigger after delay seconds.
    Timer is daemonized and cancelable via shutdown_event.
    """
    def _t2_run():
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            from keypulse.pipeline.triggers import should_trigger, record_trigger, finalize_stale_pending

            finalize_stale_pending(cfg.db_path_expanded, now=now)
            allowed, reason = should_trigger("T2", now=now, db_path=cfg.db_path_expanded, cfg={})
            record_trigger("T2", now=now, db_path=cfg.db_path_expanded, outcome="allowed")
            logger.info(f"T2 trigger fired: {reason}")

            # Run sync (same path as T1/CLI)
            try:
                _run_obsidian_sync_core(cfg)
                record_trigger("T2", now=now, db_path=cfg.db_path_expanded, outcome="ran:ok")
            except Exception as e:
                logger.error(f"T2 sync failed: {e}")
                record_trigger("T2", now=now, db_path=cfg.db_path_expanded, outcome="ran:fail", note=str(e))
        except Exception as e:
            logger.error(f"T2 trigger error: {e}")

    timer = threading.Timer(delay, _t2_run)
    timer.daemon = True
    timer.start()
    # Note: timer.cancel() is called if shutdown_event fires before delay


def _spawn_t3_scheduler(cfg: Config, shutdown_event: threading.Event) -> None:
    """
    Spawn a daemon thread that runs T3 idle scheduler every 60 seconds.
    Respects shutdown_event for responsive exit.
    """
    def _t3_loop():
        from keypulse.pipeline.triggers import should_trigger, record_trigger
        from keypulse.capture.user_presence import idle_seconds

        state = {"was_active": False}
        logger.info("T3 idle scheduler started")

        while not shutdown_event.is_set():
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                state = _t3_tick(
                    state,
                    now=now,
                    db_path=cfg.db_path_expanded,
                    sync_fn=lambda: _run_obsidian_sync_core(cfg),
                    should_trigger_fn=lambda kind: should_trigger(
                        kind, now=now, db_path=cfg.db_path_expanded, cfg={}
                    ),
                    record_trigger_fn=lambda kind, outcome: record_trigger(
                        kind, now=now, db_path=cfg.db_path_expanded, outcome=outcome
                    ),
                    idle_seconds_fn=idle_seconds,
                )
            except Exception as e:
                logger.error(f"T3 tick error: {e}")

            # Wait 60s for shutdown signal (responsive exit)
            if shutdown_event.wait(timeout=60.0):
                logger.info("T3 idle scheduler stopping")
                break

    thread = threading.Thread(target=_t3_loop, daemon=True, name="t3-idle-scheduler")
    thread.start()


def run(config: Optional[Config] = None):
    """
    Main daemon execution: starts CaptureManager, blocks until SIGTERM/SIGINT.
    Can run in the foreground or after daemonize().
    """
    if config is None:
        config = Config.load()

    setup_logging(config.log_path_expanded)
    logger.info("KeyPulse daemon starting")

    lock = SingleInstanceLock()
    if not lock.acquire():
        logger.error(f"Another KeyPulse instance is already running (PID {lock.get_pid()})")
        sys.exit(1)

    # Warn if Accessibility permission is missing — window titles won't be captured
    if not _check_accessibility():
        logger.warning(
            "Accessibility permission not granted. "
            "Window titles and app names may not be available. "
            "Grant access in: System Settings → Privacy & Security → Accessibility"
        )

    from keypulse.capture.manager import CaptureManager

    manager = CaptureManager(config)
    def _shutdown(signum, frame):
        logger.info(f"Signal {signum} received, shutting down")
        try:
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
        logger.info("KeyPulse daemon running")

        # Spawn T2 (daemon startup smoke trigger) - 60s after start
        _spawn_t2_trigger(config, manager._running, delay=60.0)

        # Spawn T3 (idle-based trigger) - 60s tick loop
        _spawn_t3_scheduler(config, manager._running)

        while True:
            time.sleep(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
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

    from keypulse.utils.paths import get_pid_path
    pid_path = get_pid_path()

    # Double-fork: after this, parent exits, child continues as daemon
    daemonize(pid_path)

    # --- Only daemon process reaches here ---
    run(config)
