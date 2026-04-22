from __future__ import annotations
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

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
