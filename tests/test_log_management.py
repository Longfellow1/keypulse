from __future__ import annotations

from logging.handlers import RotatingFileHandler
from pathlib import Path

from penpal.config import Config
from penpal.foundation import runtime_logs
from penpal.utils import logging as penpal_logging
from penpal.utils.paths import get_log_path


def _fake_home(monkeypatch, home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)


def test_legacy_log_is_renamed_to_canonical_name_once(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _fake_home(monkeypatch, home)
    data_dir = home / ".penpal"
    data_dir.mkdir()
    legacy = data_dir / "keypulse.log"
    legacy.write_text("old log\n", encoding="utf-8")

    assert Config().log_path_expanded == data_dir / "penpal.log"
    assert (data_dir / "penpal.log").read_text(encoding="utf-8") == "old log\n"
    assert not legacy.exists()
    assert get_log_path() == data_dir / "penpal.log"


def test_log_resolver_keeps_both_files_and_reports_canonical_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _fake_home(monkeypatch, home)
    data_dir = home / ".penpal"
    data_dir.mkdir()
    current = data_dir / "penpal.log"
    legacy = data_dir / "keypulse.log"
    current.write_text("new\n", encoding="utf-8")
    legacy.write_text("old\n", encoding="utf-8")

    assert get_log_path() == current
    assert current.read_text(encoding="utf-8") == "new\n"
    assert legacy.read_text(encoding="utf-8") == "old\n"


def test_log_resolver_without_existing_log_returns_canonical_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _fake_home(monkeypatch, home)

    assert Config().log_path_expanded == home / ".penpal" / "penpal.log"
    assert not (home / ".penpal" / "penpal.log").exists()
    assert not (home / ".penpal" / "keypulse.log").exists()


def test_log_resolver_maps_legacy_data_dir_config_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _fake_home(monkeypatch, home)
    legacy = home / ".keypulse"
    legacy.mkdir()
    (legacy / "keypulse.log").write_text("old log\n", encoding="utf-8")
    config = Config(app={"log_path": str(legacy / "keypulse.log")})

    assert config.log_path_expanded == home / ".penpal" / "penpal.log"
    assert (home / ".penpal" / "penpal.log").read_text(encoding="utf-8") == "old log\n"


def test_rotating_app_log_rolls_and_retains_only_configured_backups(tmp_path, monkeypatch):
    monkeypatch.setattr(penpal_logging, "APP_LOG_MAX_BYTES", 180)
    monkeypatch.setattr(penpal_logging, "APP_LOG_BACKUP_COUNT", 2)
    logger = penpal_logging.setup_logging(tmp_path / "penpal.log")
    try:
        handlers = [handler for handler in logger.handlers if getattr(handler, "_penpal_app_log", False)]
        assert len(handlers) == 1
        assert isinstance(handlers[0], RotatingFileHandler)
        penpal_logging.setup_logging(tmp_path / "penpal.log")
        handlers = [handler for handler in logger.handlers if getattr(handler, "_penpal_app_log", False)]
        assert len(handlers) == 1

        for index in range(8):
            logger.info("rotation record %02d %s", index, "x" * 24)

        assert (tmp_path / "penpal.log").exists()
        assert (tmp_path / "penpal.log.1").exists()
        assert (tmp_path / "penpal.log.2").exists()
        assert not (tmp_path / "penpal.log.3").exists()
    finally:
        for handler in tuple(logger.handlers):
            if getattr(handler, "_penpal_app_log", False):
                logger.removeHandler(handler)
                handler.close()


def test_launchd_output_logs_are_capped_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_logs, "LAUNCHD_LOG_MAX_BYTES", 20)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    for name in runtime_logs.LAUNCHD_OUTPUT_NAMES:
        (logs_dir / name).write_bytes(b"x" * 32)

    path = logs_dir / "daemon.log"
    original_inode = path.stat().st_ino
    open_launchd_handle = path.open("ab")
    try:
        errors = runtime_logs.cap_launchd_output_logs(tmp_path)
        assert errors == []
        assert path.stat().st_size == 0
        assert path.stat().st_ino == original_inode
        open_launchd_handle.write(b"still same open file\n")
        open_launchd_handle.flush()
        assert path.read_bytes() == b"still same open file\n"
    finally:
        open_launchd_handle.close()

    assert all((logs_dir / name).stat().st_size == 0 for name in runtime_logs.LAUNCHD_OUTPUT_NAMES if name != "daemon.log")
