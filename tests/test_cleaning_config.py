from __future__ import annotations

import logging
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from penpal.sources.cleaning import config


def test_missing_cleaning_config_uses_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)

    assert config.load_cleaning_config() == config.CleaningConfig()


def test_invalid_toml_stops_loading_and_logs_only_error_type(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    (tmp_path / "cleaning.toml").write_text("[broken", encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="penpal.sources.cleaning.config"):
        with pytest.raises(RuntimeError, match="cleaning.toml.*停止导入"):
            config.load_cleaning_config()

    assert "TOMLDecodeError" in caplog.text
    assert "[broken" not in caplog.text


def test_cleaning_config_read_error_stops_loading_and_logs(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    (tmp_path / "cleaning.toml").touch()

    def deny_read(self: Path, *args, **kwargs):
        raise PermissionError("private file detail")

    monkeypatch.setattr(Path, "read_text", deny_read)
    with caplog.at_level(logging.ERROR, logger="penpal.sources.cleaning.config"):
        with pytest.raises(RuntimeError, match="cleaning.toml.*停止导入"):
            config.load_cleaning_config()

    assert "PermissionError" in caplog.text
    assert "private file detail" not in caplog.text


def test_unexpected_cleaning_config_error_is_not_caught(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    (tmp_path / "cleaning.toml").touch()

    def unexpected(self: Path, *args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(Path, "read_text", unexpected)

    with pytest.raises(RuntimeError, match="unexpected"):
        config.load_cleaning_config()


def test_import_stops_before_progress_is_written_when_config_is_invalid(monkeypatch, tmp_path) -> None:
    from penpal.sources import importer

    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    (tmp_path / "cleaning.toml").write_text("[broken", encoding="utf-8")
    monkeypatch.setattr(importer, "get_source", lambda _name: object())
    monkeypatch.setattr(importer, "source_writes_allowed", lambda _name: True)
    monkeypatch.setattr(importer, "_probe", lambda _plugin: SimpleNamespace(available=True))
    monkeypatch.setattr(importer, "read_progress", lambda _name: None)
    progress_writes = []
    monkeypatch.setattr(importer, "_save_progress", lambda *args: progress_writes.append(args))

    with pytest.raises(RuntimeError, match="cleaning.toml.*停止导入"):
        importer.import_source(
            "fake",
            since=datetime(2026, 1, 1, tzinfo=timezone.utc),
            until=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

    assert progress_writes == []
