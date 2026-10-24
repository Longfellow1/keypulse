"""Legacy ~/.keypulse paths inside config.toml after the ~/.penpal migration."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from penpal.config import Config, rewrite_legacy_config_paths
from penpal.utils import paths

LEGACY_CONFIG = """\
[app]
db_path = "~/.keypulse/penpal.db"
log_path = "{abs_log}"   # absolute legacy path, comment must survive

[pipeline]
feedback_path = "~/.keypulse/feedback.jsonl"
sibling_dir = "~/.keypulse2/keep"
notes = "~/Documents/notes"
flush_interval_sec = 5
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)  # Config.load also reads ./config.toml; keep the repo's own file out of it
    return home


def _legacy_text(home: Path) -> str:
    return LEGACY_CONFIG.format(abs_log=f"{home}/.keypulse/penpal.log")


def test_rewrites_legacy_values_and_keeps_backup(home):
    data = home / ".penpal"
    data.mkdir()
    original = _legacy_text(home)
    (data / "config.toml").write_text(original, encoding="utf-8")

    assert rewrite_legacy_config_paths(data) is True

    text = (data / "config.toml").read_text(encoding="utf-8")
    parsed = tomllib.loads(text)
    assert parsed["app"]["db_path"] == "~/.penpal/penpal.db"
    assert parsed["app"]["log_path"] == f"{home}/.penpal/penpal.log"
    assert parsed["pipeline"]["feedback_path"] == "~/.penpal/feedback.jsonl"
    assert parsed["pipeline"]["sibling_dir"] == "~/.keypulse2/keep"  # a different directory is not touched
    assert parsed["pipeline"]["notes"] == "~/Documents/notes"
    assert parsed["pipeline"]["flush_interval_sec"] == 5
    assert "# absolute legacy path, comment must survive" in text
    assert (data / paths.CONFIG_BACKUP_NAME).read_text(encoding="utf-8") == original


def test_second_run_changes_nothing_and_backup_is_not_overwritten(home):
    data = home / ".penpal"
    data.mkdir()
    (data / "config.toml").write_text(_legacy_text(home), encoding="utf-8")
    assert rewrite_legacy_config_paths(data) is True
    after_first = (data / "config.toml").read_text(encoding="utf-8")
    backup = data / paths.CONFIG_BACKUP_NAME
    backup.write_text("older backup, must stay", encoding="utf-8")

    assert rewrite_legacy_config_paths(data) is False

    assert (data / "config.toml").read_text(encoding="utf-8") == after_first
    assert backup.read_text(encoding="utf-8") == "older backup, must stay"


def test_config_without_legacy_paths_is_left_alone(home):
    data = home / ".penpal"
    data.mkdir()
    (data / "config.toml").write_text('[app]\ndb_path = "~/.penpal/penpal.db"\n', encoding="utf-8")

    assert rewrite_legacy_config_paths(data) is False
    assert not (data / paths.CONFIG_BACKUP_NAME).exists()


def test_get_data_dir_rewrites_an_already_migrated_config(home):
    data = home / ".penpal"
    data.mkdir()
    (data / "config.toml").write_text(_legacy_text(home), encoding="utf-8")

    assert paths.get_data_dir() == data

    assert tomllib.loads((data / "config.toml").read_text(encoding="utf-8"))["app"]["db_path"] == "~/.penpal/penpal.db"


def test_resolve_legacy_data_path_handles_tilde_and_absolute_forms(home):
    assert paths.resolve_legacy_data_path("~/.keypulse/penpal.db") == "~/.penpal/penpal.db"
    assert paths.resolve_legacy_data_path(f"{home}/.keypulse") == f"{home}/.penpal"
    assert paths.resolve_legacy_data_path("~/.keypulse2/x") == "~/.keypulse2/x"
    assert paths.resolve_legacy_data_path("~/.penpal/penpal.db") == "~/.penpal/penpal.db"


def test_config_load_resolves_legacy_path_when_legacy_dir_is_only_the_marker(home, tmp_path):
    legacy = home / ".keypulse"
    legacy.mkdir()
    (legacy / paths.MIGRATION_MARKER).write_text(
        f"migrated_at=2026-10-19T00:00:00+00:00\nnew_path={home / '.penpal'}\n", encoding="utf-8"
    )
    (home / ".penpal").mkdir()
    # Config is read from the working directory, so the rewrite in get_data_dir does not touch it.
    (tmp_path / "config.toml").write_text(
        '[app]\ndb_path = "~/.keypulse/penpal.db"\nlog_path = "~/.keypulse/penpal.log"\n', encoding="utf-8"
    )

    cfg = Config.load()

    assert cfg.db_path_expanded == home / ".penpal" / "penpal.db"
    assert cfg.log_path_expanded == home / ".penpal" / "penpal.log"
