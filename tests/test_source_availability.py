"""Issue #22: explicit unavailable state with reason codes for source plugins. Synthetic temp files only."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from penpal.sources import availability as av
from penpal.sources import registry
from penpal.sources import discover
from penpal.sources.plugins.chrome_history import ChromeHistorySource
from penpal.sources.plugins.markdown_vault import MarkdownVaultSource
from penpal.sources.plugins.safari_history import SafariHistorySource
from penpal.sources.plugins.zsh_history import ZshHistorySource
from penpal.sources.types import DataSourceInstance

SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)
UNTIL = datetime(2030, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake = tmp_path / "home"
    fake.mkdir()
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setattr(Path, "home", lambda: fake)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_zsh_missing_file_is_unavailable_with_reason_not_exception(home):
    src = ZshHistorySource(history_path=home / "absent_history")
    state = src.availability()
    assert (state.available, state.reason, state.status) == (False, "file_missing", "unavailable")
    # A missing file reads as zero rows; the reason lives in availability(), not in an exception.
    instance = DataSourceInstance(plugin=src.name, locator=str(home / "absent_history"), label="x")
    assert list(src.read(instance, SINCE, UNTIL)) == []


def test_zsh_available_when_readable(home):
    path = home / "hist"
    path.write_text(": 1790000000:0;echo ok\n")
    assert ZshHistorySource(history_path=path).availability() == av.AVAILABLE


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root can read any file")
def test_zsh_unreadable_file_is_permission_denied(home):
    path = home / "hist"
    path.write_text(": 1790000000:0;echo ok\n")
    path.chmod(0)
    try:
        assert ZshHistorySource(history_path=path).availability().reason == "permission_denied"
    finally:
        path.chmod(0o600)


def test_chrome_schema_mismatch_is_unsupported_version(home):
    db = home / "chrome" / "Default" / "History"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE downloads (id INTEGER PRIMARY KEY)")  # no urls / visits tables
    conn.commit()
    conn.close()
    state = ChromeHistorySource(profiles_root=home / "chrome").availability()
    assert state.reason == "unsupported_version"


def test_chrome_no_profile_is_file_missing(home):
    assert ChromeHistorySource(profiles_root=home / "chrome").availability().reason == "file_missing"


def test_safari_not_a_database_is_unsupported_version(home):
    db = home / "History.db"
    db.write_bytes(b"this is not sqlite" * 10)
    assert SafariHistorySource(history_path=db).availability().reason == "unsupported_version"


def test_locked_database_maps_to_db_locked(home, monkeypatch):
    db = home / "History.db"
    db.write_bytes(b"x")

    def _locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(av.sqlite3, "connect", _locked)
    assert av.check_sqlite(db, required_tables=("a",)).reason == "db_locked"


def test_markdown_without_any_approved_root_is_not_found(home):
    src = MarkdownVaultSource(roots=None)
    assert src.availability().reason == "not_found"


def test_markdown_missing_root_is_file_missing(home):
    src = MarkdownVaultSource(roots=[home / "no_vault"])
    assert src.availability().reason == "file_missing"


def test_unknown_reason_code_is_rejected():
    with pytest.raises(ValueError):
        av.unavailable("whatever")


def test_registry_reports_each_plugin_and_filters_by_allowed_names(home, monkeypatch):
    zsh = ZshHistorySource(history_path=home / "absent")
    monkeypatch.setitem(registry._PLUGINS, "zsh_history", zsh)
    rows = {row["source"]: row["reason"] for row in registry.unavailable_sources(["zsh_history"])}
    assert rows == {"zsh_history": "file_missing"}
    assert all(row["source"] == "zsh_history" for row in registry.unavailable_sources(["zsh_history"]))


def test_one_probe_that_raises_does_not_break_others(home, monkeypatch):
    class Boom(ZshHistorySource):
        name = "boom_source"

        def availability(self):
            raise RuntimeError("probe exploded")

    monkeypatch.setitem(registry._PLUGINS, "boom_source", Boom(history_path=home / "x"))
    rows = dict((plugin.name, state) for plugin, state in registry.source_availability("boom_source"))
    assert rows["boom_source"].reason == "probe_error"


def test_health_snapshot_lists_only_unavailable_cheap_sources(home, monkeypatch):
    from penpal.health import check

    monkeypatch.setitem(registry._PLUGINS, "zsh_history", ZshHistorySource(history_path=home / "absent"))
    rows = check._unavailable_sources_snapshot()
    assert {"source": "zsh_history", "reason": "file_missing"} in rows
    # not_found (nothing configured) is not a failed read and stays out of health / doctor / MCP coverage
    assert all(row["reason"] != "not_found" for row in rows)


def test_sources_list_shows_availability_column(home, monkeypatch):
    from click.testing import CliRunner

    monkeypatch.setattr(discover.Config, "load", classmethod(lambda cls: SimpleNamespace(db_path_expanded=home / "t.db", watchers=SimpleNamespace(keyboard_chunk=True))))
    monkeypatch.setattr(discover, "disabled_sources", lambda: frozenset())
    monkeypatch.setitem(registry._PLUGINS, "zsh_history", ZshHistorySource(history_path=home / "absent"))
    result = CliRunner().invoke(discover.sources_group, ["list"])
    assert result.exit_code == 0, result.output
    line = next(line for line in result.output.splitlines() if line.startswith("zsh_history\t"))
    assert "[unavailable: file_missing]" in line
