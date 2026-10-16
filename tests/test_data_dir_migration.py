from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from penpal.config import Config
from penpal.utils import paths

# Captured at import, before conftest's autouse stub replaces it for each test.
_REAL_LEGACY_PROCESS_SCAN = paths._find_legacy_mcp_serve_processes


def _set_home(monkeypatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)


def test_new_install_creates_penpal_directory(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)

    data_dir = paths.get_data_dir()

    assert data_dir == tmp_path / ".penpal"
    assert data_dir.is_dir()
    assert not (tmp_path / ".keypulse").exists()


def test_existing_keypulse_directory_moves_atomically_and_leaves_marker(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    old_dir = tmp_path / ".keypulse"
    old_dir.mkdir()
    database = old_dir / "keypulse.db"
    database.write_bytes(b"existing database bytes")

    data_dir = paths.get_data_dir()

    marker_dir = tmp_path / ".keypulse"
    marker = marker_dir / paths.MIGRATION_MARKER
    assert data_dir == tmp_path / ".penpal"
    assert (data_dir / "keypulse.db").read_bytes() == b"existing database bytes"
    assert sorted(path.name for path in marker_dir.iterdir()) == [paths.MIGRATION_MARKER]
    marker_text = marker.read_text(encoding="utf-8")
    assert "migrated_at=" in marker_text
    assert f"new_path={data_dir}" in marker_text


def test_live_legacy_pid_refuses_migration_without_changing_directories(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    old_dir = tmp_path / ".keypulse"
    old_dir.mkdir()
    old_file = old_dir / "keypulse.db"
    old_file.write_bytes(b"legacy data")
    (old_dir / "keypulse.pid").write_text("4567\n", encoding="utf-8")
    monkeypatch.setattr(paths, "_pid_is_alive", lambda pid: pid == 4567)
    monkeypatch.setattr(paths, "_find_legacy_mcp_serve_processes", lambda: [])

    with pytest.raises(paths.DataDirMigrationError, match="old KeyPulse process is running"):
        paths.get_data_dir()

    assert old_file.read_bytes() == b"legacy data"
    assert (old_dir / "keypulse.pid").read_text(encoding="utf-8") == "4567\n"
    assert not (tmp_path / ".penpal").exists()


def test_stale_legacy_pid_allows_migration(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    old_dir = tmp_path / ".keypulse"
    old_dir.mkdir()
    (old_dir / "keypulse-hud.pid").write_text("4567\n", encoding="utf-8")
    monkeypatch.setattr(paths, "_pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(paths, "_find_legacy_mcp_serve_processes", lambda: [])

    data_dir = paths.get_data_dir()

    assert data_dir == tmp_path / ".penpal"
    assert paths._is_migration_marker_dir(tmp_path / ".keypulse", data_dir)


def test_mocked_legacy_mcp_process_refuses_migration(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    old_dir = tmp_path / ".keypulse"
    old_dir.mkdir()
    (old_dir / "keypulse.db").write_bytes(b"legacy data")
    monkeypatch.setattr(paths, "_find_legacy_mcp_serve_processes", lambda: ["keypulse mcp serve (PID 4568)"])

    with pytest.raises(paths.DataDirMigrationError, match=r"keypulse mcp serve \(PID 4568\)"):
        paths.get_data_dir()

    assert (old_dir / "keypulse.db").read_bytes() == b"legacy data"
    assert not (tmp_path / ".penpal").exists()


def test_missing_pgrep_warns_but_does_not_block_migration(tmp_path, monkeypatch, capsys):
    _set_home(monkeypatch, tmp_path)
    old_dir = tmp_path / ".keypulse"
    old_dir.mkdir()
    (old_dir / "keypulse.pid").write_text("4567\n", encoding="utf-8")
    monkeypatch.setattr(paths, "_pid_is_alive", lambda _pid: False)

    def missing_pgrep(*_args, **_kwargs):
        raise FileNotFoundError("pgrep")

    monkeypatch.setattr(paths.subprocess, "run", missing_pgrep)
    monkeypatch.setattr(paths, "_find_legacy_mcp_serve_processes", _REAL_LEGACY_PROCESS_SCAN)

    data_dir = paths.get_data_dir()

    assert data_dir == tmp_path / ".penpal"
    assert "could not check for old KeyPulse MCP processes" in capsys.readouterr().err


def test_both_non_marker_directories_stop_without_changes(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    old_file = tmp_path / ".keypulse" / "old.txt"
    new_file = tmp_path / ".penpal" / "new.txt"
    old_file.parent.mkdir()
    new_file.parent.mkdir()
    old_file.write_text("old", encoding="utf-8")
    new_file.write_text("new", encoding="utf-8")

    with pytest.raises(paths.DataDirMigrationError, match="Both .* exist"):
        paths.get_data_dir()

    assert old_file.read_text(encoding="utf-8") == "old"
    assert new_file.read_text(encoding="utf-8") == "new"


def test_cross_device_rename_stops_without_copy_fallback(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    old_file = tmp_path / ".keypulse" / "old.txt"
    old_file.parent.mkdir()
    old_file.write_text("keep", encoding="utf-8")

    def raise_exdev(_source, _target):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(paths.os, "rename", raise_exdev)
    with pytest.raises(paths.DataDirMigrationError, match="different filesystems"):
        paths.get_data_dir()

    assert old_file.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / ".penpal").exists()


def test_symlinked_destination_is_not_replaced(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    old_dir = tmp_path / ".keypulse"
    old_dir.mkdir()
    (old_dir / "old.txt").write_text("keep", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / ".penpal"
    destination.symlink_to(outside, target_is_directory=True)

    with pytest.raises(paths.DataDirMigrationError, match="symbolic link"):
        paths.get_data_dir()

    assert (old_dir / "old.txt").read_text(encoding="utf-8") == "keep"
    assert destination.is_symlink()
    assert list(outside.iterdir()) == []


def test_repeat_migration_is_idempotent(tmp_path):
    old_file = tmp_path / ".keypulse" / "data.txt"
    old_file.parent.mkdir()
    old_file.write_text("keep", encoding="utf-8")

    assert paths.migrate_legacy_data_dir(tmp_path) is True
    assert paths.migrate_legacy_data_dir(tmp_path) is False
    assert (tmp_path / ".penpal" / "data.txt").read_text(encoding="utf-8") == "keep"
    assert paths._is_migration_marker_dir(tmp_path / ".keypulse", tmp_path / ".penpal")


def test_marker_write_interruption_rolls_directory_back(tmp_path, monkeypatch):
    old_file = tmp_path / ".keypulse" / "data.txt"
    old_file.parent.mkdir()
    old_file.write_text("keep", encoding="utf-8")
    original_write_text = Path.write_text

    def fail_marker_write(path, *args, **kwargs):
        if path.name == paths.MIGRATION_MARKER:
            raise OSError("simulated marker write interruption")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_marker_write)
    with pytest.raises(paths.DataDirMigrationError, match="rolled back"):
        paths.migrate_legacy_data_dir(tmp_path)

    assert old_file.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / ".penpal").exists()


def test_manual_rollback_removes_marker_then_renames_data_back(tmp_path):
    old_file = tmp_path / ".keypulse" / "data.txt"
    old_file.parent.mkdir()
    old_file.write_text("keep", encoding="utf-8")

    assert paths.migrate_legacy_data_dir(tmp_path) is True
    marker_dir = tmp_path / ".keypulse"
    (marker_dir / paths.MIGRATION_MARKER).unlink()
    marker_dir.rmdir()
    os.rename(tmp_path / ".penpal", marker_dir)

    assert old_file.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / ".penpal").exists()


def test_config_reads_migrated_database_at_old_filename(tmp_path, monkeypatch):
    _set_home(monkeypatch, tmp_path)
    old_dir = tmp_path / ".keypulse"
    old_dir.mkdir()
    old_db = old_dir / "keypulse.db"
    old_db.write_bytes(b"database")
    (old_dir / "config.toml").write_text(
        '[app]\ndb_path = "~/.keypulse/keypulse.db"\nlog_path = "~/.keypulse/keypulse.log"\n',
        encoding="utf-8",
    )

    paths.get_data_dir()
    config = Config.load()

    assert config.db_path_expanded == tmp_path / ".penpal" / "keypulse.db"
    assert config.db_path_expanded.read_bytes() == b"database"
    assert config.log_path_expanded == tmp_path / ".penpal" / "keypulse.log"
