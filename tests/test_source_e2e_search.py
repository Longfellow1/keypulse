"""Issue #22 end-to-end: a synthetic fixture for each of zsh_history, chrome_history, safari_history and
markdown_vault goes through import (registry.read_all), storage, MCP search_context (finds it, with its
source in coverage) and get_evidence (opens the original). When the original disappears, the source is
reported as unavailable in the coverage note and nothing raises. Synthetic data only; HOME is a temp dir."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from penpal.mcp import auth, tools
from penpal.sources import registry
from penpal.sources.plugins.chrome_history import ChromeHistorySource
from penpal.sources.plugins.markdown_vault import MarkdownVaultSource
from penpal.sources.plugins.safari_history import SafariHistorySource
from penpal.sources.plugins.zsh_history import ZshHistorySource
from penpal.store.buckets import BUCKETS
from penpal.store.db import close, init_db
from mcp_helpers import client_of

SINCE = datetime(2026, 1, 1, tzinfo=timezone.utc)
UNTIL = datetime(2030, 1, 1, tzinfo=timezone.utc)
WEBKIT_US = 11_644_473_600 * 1_000_000
CF_S = 978_307_200
VISIT_TS = datetime(2026, 4, 28, 1, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.chdir(tmp_path)
    init_db(tmp_path / "e2e.db")
    yield tmp_path
    close()


def _token() -> str:
    return auth.create_grant("host", buckets=list(BUCKETS))


def _write_zsh(root: Path) -> tuple[ZshHistorySource, Path]:
    path = root / "zsh_hist"
    path.write_text(": 1790000000:0;echo quokkazsh deploy\n: 1790000100:0;ls -la\n")
    return ZshHistorySource(history_path=path), path


def _write_chrome(root: Path) -> tuple[ChromeHistorySource, Path]:
    db = root / "chrome" / "Default" / "History"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
    conn.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
    conn.execute("INSERT INTO urls VALUES (1, 'https://example.com/page', 'Quokkachrome synthetic page')")
    conn.execute("INSERT INTO visits VALUES (10, 1, ?)", (int(VISIT_TS * 1_000_000) + WEBKIT_US,))
    conn.commit()
    conn.close()
    return ChromeHistorySource(profiles_root=root / "chrome"), db


def _write_safari(root: Path) -> tuple[SafariHistorySource, Path]:
    db = root / "safari" / "History.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
    conn.execute("CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER, title TEXT, visit_time REAL)")
    conn.execute("INSERT INTO history_items VALUES (1, 'https://example.org/doc')")
    conn.execute("INSERT INTO history_visits VALUES (20, 1, 'Quokkasafari synthetic doc', ?)", (VISIT_TS - CF_S,))
    conn.commit()
    conn.close()
    return SafariHistorySource(history_path=db), db


def _write_markdown(root: Path) -> tuple[MarkdownVaultSource, Path]:
    vault = root / "vault"
    vault.mkdir()
    note = vault / "idea.md"
    note.write_text("---\ntags: x\n---\n\n# Quokkamarkdown plan\nsynthetic body\n")
    return MarkdownVaultSource(roots=[vault]), note


WRITERS = {
    "zsh_history": (_write_zsh, "quokkazsh"),
    "chrome_history": (_write_chrome, "quokkachrome"),
    "safari_history": (_write_safari, "quokkasafari"),
    "markdown_vault": (_write_markdown, "quokkamarkdown"),
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_fixture_import_store_search_and_open_evidence(home, monkeypatch, name):
    writer, keyword = WRITERS[name]
    source, original = writer(home)
    monkeypatch.setitem(registry._PLUGINS, name, source)  # the import path uses the registered plugin

    imported = list(registry.read_all(SINCE, UNTIL, source=name))
    assert imported, f"{name}: import produced no events"

    token = _token()
    found = tools.search_context(token, [keyword], sources=[name], client=client_of(token))
    assert found["status"] == "ok", found
    hit = next(r for r in found["results"] if r["source"] == name)
    assert hit["evidence_available"] is True
    span = {s["source"]: s for s in found["coverage"]["by_source"]}[name]
    assert span["has_events_in_range"] is True
    assert name not in {row["source"] for row in found["coverage"]["unavailable_sources"]}

    opened = tools.get_evidence(token, hit["evidence_id"], client=client_of(token))
    assert opened["status"] == "ok", opened
    locator = opened["evidence"]["locator"]
    if name in ("chrome_history", "safari_history"):
        assert locator["type"] == "browser_visit" and locator["db"] == str(original.resolve())
    else:
        assert locator["type"] == "file" and locator["path"] == str(original.resolve())
    assert keyword in opened["evidence"]["excerpt"].lower()


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_missing_original_is_reported_unavailable_not_raised(home, monkeypatch, name):
    writer, keyword = WRITERS[name]
    source, original = writer(home)
    monkeypatch.setitem(registry._PLUGINS, name, source)
    list(registry.read_all(SINCE, UNTIL, source=name))
    original.unlink()
    if name == "markdown_vault":
        original.parent.rmdir()  # the approved vault root itself is gone

    token = _token()
    result = tools.search_context(token, ["unrelatedzzword"], sources=[name], client=client_of(token))
    assert result["status"] in ("no_results", "ok")
    unavailable = {row["source"]: row["reason"] for row in result["coverage"]["unavailable_sources"]}
    assert unavailable.get(name) == "file_missing"
    assert "NOT searched" in result["coverage"]["note"]
