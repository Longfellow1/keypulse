import os
import signal
from pathlib import Path
from keypulse.utils.paths import get_pid_path


class SingleInstanceLock:
    def __init__(self):
        self.pid_path = get_pid_path()

    def acquire(self) -> bool:
        """
        Atomically acquire lock using O_CREAT|O_EXCL.
        Returns True if acquired, False if another instance is running.
        Stale lock files (left after crash) are detected and removed automatically.
        """
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Atomic create — raises FileExistsError if file already exists
            fd = os.open(
                str(self.pid_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o644,
            )
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # Lock file exists — check if owning process is still alive
            try:
                pid = int(self.pid_path.read_text().strip())
                os.kill(pid, 0)  # signal 0: probe only, no actual signal sent
                return False  # Process alive → already running
            except (ProcessLookupError, PermissionError):
                # Stale lock (process gone) → clean up and retry
                self.pid_path.unlink(missing_ok=True)
                return self.acquire()
            except ValueError:
                # Corrupt PID file → clean up and retry
                self.pid_path.unlink(missing_ok=True)
                return self.acquire()

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
