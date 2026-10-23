"""Issue #29: keyboard capture risk notice (shown once) and the on/off state in doctor and source list.

All tests use a temp DB via init_db(tmp_path); nothing touches ~/.penpal.
"""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner

from penpal import cli
from penpal.capture import keyboard_notice
from penpal.capture.capture_control import set_source_enabled
from penpal.config import Config
from penpal.sources import discover
from penpal.store.db import close, init_db


@pytest.fixture(autouse=True)
def _zh_locale(monkeypatch):
    # The CLI picks zh or en from the environment; pin zh so assertions can name the exact wording.
    monkeypatch.setattr("penpal.i18n._LANG_CACHE", "zh")


@pytest.fixture
def cfg(tmp_path):
    db = tmp_path / "kp.db"
    init_db(db)
    yield Config.model_validate({"app": {"db_path": str(db)}})
    close()


@pytest.fixture
def install_env(monkeypatch, tmp_path, cfg):
    """Keep `install init` / `install onboard` inside tmp_path."""
    monkeypatch.setattr(cli, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(cli, "_install_logs_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(cli, "_write_default_user_config", lambda force=False: "skipped")
    monkeypatch.setattr(cli.Config, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(cli, "_install_launchd_jobs", lambda **kwargs: [])
    return cfg


def test_notice_states_only_what_the_code_does():
    text = keyboard_notice.notice_text()

    assert "只保存经过过滤的文本片段，不逐键保存。" in text
    assert "过滤是尽力而为的，仍可能记录到敏感内容。" in text
    assert keyboard_notice.DISABLE_COMMAND in text
    # Must not promise a search exclusion that the code does not implement (see PR description).
    assert "排除" not in text and "excluded" not in text


def test_install_init_shows_notice_exactly_once(install_env):
    runner = CliRunner()
    notice = keyboard_notice.notice_text()

    first = runner.invoke(cli.main, ["install", "init", "--no-launchd", "--plain"])
    second = runner.invoke(cli.main, ["install", "init", "--no-launchd", "--plain"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert first.output.count(notice) == 1
    assert second.output.count(notice) == 0


def test_onboard_shows_notice_only_if_init_did_not(install_env, monkeypatch):
    runner = CliRunner()
    notice = keyboard_notice.notice_text()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr("penpal.permissions.PERMISSION_SPECS", ())

    onboard = runner.invoke(cli.main, ["install", "onboard", "--non-interactive"])
    again = runner.invoke(cli.main, ["install", "onboard", "--non-interactive"])

    assert onboard.exit_code == 0, onboard.output
    assert onboard.output.count(notice) == 1
    assert again.output.count(notice) == 0


def test_doctor_plain_shows_keyboard_on_and_off_with_command(install_env, monkeypatch, tmp_path):
    from penpal.health import capture_health

    monkeypatch.setattr(capture_health, "permission_report", lambda *a, **k: [])
    monkeypatch.setattr(cli, "get_config", lambda: install_env)
    monkeypatch.setattr(cli, "get_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(cli, "_load_capture_runtime_state", lambda: None)
    (tmp_path / "config.toml").write_text("x=1")
    runner = CliRunner()

    on_output = runner.invoke(cli.main, ["doctor", "--plain"]).output
    assert "键盘文本片段（keyboard_chunk）: ON; to turn off: penpal source disable keyboard_chunk" in on_output

    set_source_enabled("keyboard_chunk", False)
    off_output = runner.invoke(cli.main, ["doctor", "--plain"]).output
    assert "键盘文本片段（keyboard_chunk）: OFF; to turn on again: penpal source enable keyboard_chunk" in off_output


def test_source_list_shows_keyboard_state_and_switch_command(monkeypatch, cfg):
    monkeypatch.setattr(discover.Config, "load", classmethod(lambda cls: cfg))
    runner = CliRunner()

    on = runner.invoke(discover.sources_group, ["list"]).output
    kb_line = next(line for line in on.splitlines() if line.startswith("keyboard_chunk\t"))
    assert "[on]" in kb_line and "penpal source disable keyboard_chunk" in kb_line

    set_source_enabled("keyboard_chunk", False)
    off = runner.invoke(discover.sources_group, ["list"]).output
    kb_line = next(line for line in off.splitlines() if line.startswith("keyboard_chunk\t"))
    assert "[off]" in kb_line and "penpal source enable keyboard_chunk" in kb_line


def test_watcher_config_off_reads_as_off_in_source_list(monkeypatch, tmp_path):
    cfg = Config.model_validate({"app": {"db_path": str(tmp_path / "kp.db")}, "watchers": {"keyboard_chunk": False}})
    init_db(cfg.db_path_expanded)
    monkeypatch.setattr(discover.Config, "load", classmethod(lambda cls: cfg))
    try:
        output = CliRunner().invoke(discover.sources_group, ["list"]).output
    finally:
        close()
    kb_line = next(line for line in output.splitlines() if line.startswith("keyboard_chunk\t"))
    assert "[off]" in kb_line
