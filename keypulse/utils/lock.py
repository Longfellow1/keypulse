import os
import signal
from pathlib import Path
from keypulse.utils.paths import get_pid_path


class SingleInstanceLock:
    def __init__(self):
        self.pid_path = get_pid_path()

    def acquire(self) -> bool:
        """Try to acquire lock. Returns True if acquired, False if already running."""
        if self.pid_path.exists():
            try:
                pid = int(self.pid_path.read_text().strip())
                os.kill(pid, 0)  # Check if process exists
                return False  # Already running
            except (ProcessLookupError, ValueError):
                pass  # Stale PID file
        self.pid_path.write_text(str(os.getpid()))
        return True

    def release(self):
        if self.pid_path.exists():
            try:
                pid = int(self.pid_path.read_text().strip())
                if pid == os.getpid():
                    self.pid_path.unlink()
            except (ValueError, OSError):
                pass

    def get_pid(self) -> int | None:
        """Return PID of running instance, or None."""
        if not self.pid_path.exists():
            return None
        try:
            pid = int(self.pid_path.read_text().strip())
            os.kill(pid, 0)
            return pid
        except (ProcessLookupError, ValueError, OSError):
            return None

    def is_running(self) -> bool:
        return self.get_pid() is not None
