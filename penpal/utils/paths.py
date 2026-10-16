from __future__ import annotations

import errno
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_MARKER = "MIGRATED_TO_PENPAL"


class DataDirMigrationError(RuntimeError):
    """The old data directory cannot be moved safely to the Penpal location."""


def _pid_is_alive(pid: int) -> bool:
    """Return whether a PID exists, including processes owned by another user."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise
    return True


def _find_legacy_mcp_serve_processes() -> list[str]:
    """Find identifiable old ``keypulse mcp serve`` processes when pgrep exists."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", r"keypulse.*mcp serve"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"Warning: could not check for old KeyPulse MCP processes ({exc}); continuing.", file=sys.stderr)
        return []

    if result.returncode == 1:
        return []
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        suffix = f": {detail}" if detail else ""
        print(
            f"Warning: pgrep could not check for old KeyPulse MCP processes{suffix}; continuing.",
            file=sys.stderr,
        )
        return []

    current_pid = os.getpid()
    processes = []
    for line in (result.stdout or "").splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid != current_pid:
            processes.append(f"keypulse mcp serve (PID {pid})")
    return processes


def _running_legacy_processes(old: Path) -> list[str]:
    processes = []
    for filename in ("keypulse.pid", "keypulse-hud.pid"):
        pid_file = old / filename
        try:
            raw_pid = pid_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        except OSError as exc:
            print(f"Warning: could not read legacy PID file {pid_file} ({exc}); continuing.", file=sys.stderr)
            continue

        try:
            pid = int(raw_pid)
            if pid <= 0:
                raise ValueError
        except ValueError:
            print(f"Warning: ignoring invalid legacy PID file {pid_file}; continuing.", file=sys.stderr)
            continue

        try:
            alive = _pid_is_alive(pid)
        except OSError as exc:
            print(f"Warning: could not check legacy PID {pid} from {pid_file} ({exc}); continuing.", file=sys.stderr)
            continue
        if alive:
            processes.append(f"{filename} PID {pid}")

    processes.extend(_find_legacy_mcp_serve_processes())
    return processes


def _is_migration_marker_dir(old: Path, new: Path) -> bool:
    if old.is_symlink():
        return False
    try:
        children = list(old.iterdir())
        if (len(children) != 1 or children[0].name != MIGRATION_MARKER
                or children[0].is_symlink() or not children[0].is_file()):
            return False
        lines = children[0].read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    return any(line.startswith("migrated_at=") for line in lines) and f"new_path={new}" in lines


def migrate_legacy_data_dir(home: Path | None = None) -> bool:
    """Atomically move ``~/.keypulse`` to ``~/.penpal`` once.

    Returns True only when this call moved the directory. It never copies, merges, or
    replaces data. If marker creation fails, it attempts to put the moved directory
    back at the original path before reporting the error.
    """
    home = home or Path.home()
    old = home / ".keypulse"
    new = home / ".penpal"

    if new.is_symlink():
        raise DataDirMigrationError(f"Cannot use {new}: expected a real Penpal data directory, not a symbolic link.")
    if new.exists() and not new.is_dir():
        raise DataDirMigrationError(f"Cannot use {new}: expected a Penpal data directory.")

    if new.exists():
        if not old.exists() and not old.is_symlink():
            return False
        if _is_migration_marker_dir(old, new):
            return False
        raise DataDirMigrationError(
            f"Both {old} and {new} exist, and {old} is not Penpal's migration marker directory. "
            "No files were changed. Resolve the directory conflict manually, then retry."
        )

    if old.is_symlink():
        raise DataDirMigrationError(f"Cannot migrate {old}: expected a real directory, not a symbolic link.")
    if not old.exists():
        return False
    if _is_migration_marker_dir(old, new):
        raise DataDirMigrationError(
            f"{old} contains a Penpal migration marker, but the destination {new} is missing. "
            "Restore the data directory or remove the marker only after confirming the data is safe."
        )
    if not old.is_dir():
        raise DataDirMigrationError(f"Cannot migrate {old}: expected a real directory.")

    running_processes = _running_legacy_processes(old)
    if running_processes:
        details = ", ".join(running_processes)
        raise DataDirMigrationError(
            f"Cannot migrate {old} while an old KeyPulse process is running ({details}). "
            "Stop the old service and exit AI clients using KeyPulse MCP, then retry; "
            "or run `bash install.sh` to unload old launchd services before migration."
        )

    try:
        os.rename(old, new)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise DataDirMigrationError(
                f"Cannot migrate {old} to {new}: they are on different filesystems. "
                "Penpal requires an atomic same-filesystem rename and will not copy the data."
            ) from exc
        raise DataDirMigrationError(f"Could not atomically rename {old} to {new}: {exc}") from exc

    marker_dir_created = False
    try:
        old.mkdir()
        marker_dir_created = True
        marker = old / MIGRATION_MARKER
        marker.write_text(
            f"migrated_at={datetime.now(timezone.utc).isoformat()}\nnew_path={new}\n",
            encoding="utf-8",
        )
    except Exception as exc:
        if marker_dir_created:
            try:
                marker = old / MIGRATION_MARKER
                if marker.exists():
                    marker.unlink()
                old.rmdir()
                os.rename(new, old)
            except Exception as rollback_exc:
                raise DataDirMigrationError(
                    f"Migration marker creation failed ({exc}); automatic rollback also failed "
                    f"({rollback_exc}). The data remains at {new}; do not delete either directory."
                ) from exc
        elif not old.exists():
            try:
                os.rename(new, old)
            except Exception as rollback_exc:
                raise DataDirMigrationError(
                    f"Migration marker creation failed ({exc}); automatic rollback also failed "
                    f"({rollback_exc}). The data remains at {new}; do not delete either directory."
                ) from exc
        else:
            raise DataDirMigrationError(
                f"The data moved to {new}, but {old} became occupied before the migration marker "
                "could be created. Neither directory was overwritten; resolve this conflict manually."
            ) from exc
        raise DataDirMigrationError(
            f"Migration marker creation failed ({exc}); the data directory was rolled back to {old}."
        ) from exc

    return True


def get_data_dir() -> Path:
    moved = migrate_legacy_data_dir()
    d = Path.home() / ".penpal"
    d.mkdir(parents=True, exist_ok=True)
    if moved:
        print(
            "Penpal moved ~/.keypulse to ~/.penpal. The moved developer venv contains scripts "
            "with an obsolete absolute path; run `bash install.sh --dev` to rebuild it.",
            file=sys.stderr,
        )
    return d


def get_db_path() -> Path:
    current = get_data_dir() / "penpal.db"
    legacy = current.with_name("keypulse.db")
    return legacy if not current.exists() and legacy.exists() else current


def get_pid_path() -> Path:
    return get_data_dir() / "penpal.pid"


def get_hud_pid_path() -> Path:
    return get_data_dir() / "penpal-hud.pid"


def get_log_path() -> Path:
    current = get_data_dir() / "penpal.log"
    legacy = current.with_name("keypulse.log")
    return legacy if not current.exists() and legacy.exists() else current


def get_config_path() -> Path:
    return get_data_dir() / "config.toml"
