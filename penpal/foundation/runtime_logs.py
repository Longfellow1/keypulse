"""Bound launchd-owned stdout/stderr files without replacing their open inodes."""
from __future__ import annotations

from pathlib import Path

from penpal.utils.paths import get_data_dir


LAUNCHD_LOG_MAX_BYTES = 1024 * 1024
LAUNCHD_LOG_CHECK_INTERVAL_SEC = 30
DAEMON_STDOUT_NAME = "daemon.log"
DAEMON_STDERR_NAME = "daemon.err"
HEALTHCHECK_STDOUT_NAME = "healthcheck.log"
HEALTHCHECK_STDERR_NAME = "healthcheck.err"
LAUNCHD_OUTPUT_NAMES = (
    DAEMON_STDOUT_NAME,
    DAEMON_STDERR_NAME,
    HEALTHCHECK_STDOUT_NAME,
    HEALTHCHECK_STDERR_NAME,
)


def cap_launchd_output_logs(data_dir: Path | None = None) -> list[tuple[Path, OSError]]:
    """Copy-truncate oversized launchd output files, keeping launchd's fd valid."""
    root = data_dir if data_dir is not None else get_data_dir()
    errors: list[tuple[Path, OSError]] = []
    for name in LAUNCHD_OUTPUT_NAMES:
        path = root / "logs" / name
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size <= LAUNCHD_LOG_MAX_BYTES:
                continue
            # Launchd owns an open descriptor for these paths. Truncating in place
            # preserves that inode; rename-and-recreate would leave writes on an
            # unbounded, renamed file.
            with path.open("r+b") as handle:
                handle.truncate(0)
        except OSError as exc:
            errors.append((path, exc))
    return errors
