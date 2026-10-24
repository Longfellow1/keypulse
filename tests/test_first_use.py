"""Issue #32: first use from the CLI alone (D33).

- AC1: a fresh temp HOME with only Claude Code chosen completes `penpal install onboard`, imports synthetic
  session files, and search_context returns a hit.
- AC2: a first import in batches keeps a persisted cursor. A stop after N batches (or a failure in the middle of
  a batch) leaves stored batches queryable, and a rerun resumes from the cursor without duplicate rows.
  `penpal status` shows the progress.
- AC3: one source's permission probe returns denied. The other chosen source still imports and is searchable,
  and onboarding and doctor report the denied one with its fix path.

Everything runs under a temp HOME with synthetic fixtures. Registered plugins are replaced by instances that
point at the temp HOME, so no real ~/.claude, ~/Library or ~/.penpal is read.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from penpal import cli
from penpal.cli import doctor as doctor_cli
from penpal.cli import install as install_cli
from penpal.capture.capture_control import disabled_sources
from penpal.mcp import auth, tools
from penpal.sources import importer, registry
from penpal.sources.availability import PERMISSION_DENIED, unavailable
from penpal.sources.onboarding import PANE_FULL_DISK_ACCESS
from penpal.sources.plugins.claude_code import ClaudeCodeSource
from penpal.sources.plugins.safari_history import SafariHistorySource
from penpal.store.buckets import BUCKETS
from penpal.store.db import close, init_db
from penpal.store.repository import get_conn
from penpal.store.sync_repo import get_cursor
from mcp_helpers import client_of

CF_S = 978_307_200  # Core Foundation epoch offset used by Safari history rows


@pytest.fixture(autouse=True)
def _zh_locale(monkeypatch):
    monkeypatch.setattr("penpal.i18n._LANG_CACHE", "zh")


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / ".penpal").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.chdir(tmp_path)  # Config.load() falls back to ./config.toml; keep it out of the repo
    init_db(fake_home / ".penpal" / "penpal.db")
    # Install-time side effects: no launchctl, no real plists, no real MCP host configs.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("penpal.permissions.PERMISSION_SPECS", ())
    monkeypatch.setattr(install_cli.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(install_cli, "_launchd_label_loaded", lambda label: False)
    monkeypatch.setattr(install_cli, "_launchd_plist_path", lambda label: fake_home / "Library" / f"{label}.plist")
    monkeypatch.setattr(doctor_cli, "_legacy_mcp_config_findings", lambda home=None: [])
    yield fake_home
    close()


def _write_claude_session(home: Path, *, n: int, keyword: str, name: str = "session-a.jsonl") -> Path:
    project = home / ".claude" / "projects" / "-Users-demo-notes"
    project.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(n):
        ts = (now - timedelta(minutes=n - i)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        rows.append(json.dumps({
            "type": "user" if i % 2 == 0 else "assistant",
            "uuid": f"{name}-m{i}",
            "sessionId": name.removesuffix(".jsonl"),
            "timestamp": ts,
            "message": {"role": "user", "content": f"{keyword} synthetic note {i} about the release plan"},
        }))
    path = project / name
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _write_safari(home: Path, *, keyword: str) -> SafariHistorySource:
    db = home / "Library" / "Safari" / "History.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
    conn.execute("CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER, title TEXT, visit_time REAL)")
    conn.execute("INSERT INTO history_items VALUES (1, 'https://example.org/doc')")
    conn.execute(
        "INSERT INTO history_visits VALUES (20, 1, ?, ?)",
        (f"{keyword} synthetic doc", time.time() - CF_S),
    )
    conn.commit()
    conn.close()
    return SafariHistorySource(history_path=db)


@pytest.fixture
def claude_plugin(home, monkeypatch):
    """Registry holding only the claude_code plugin, pointed at the temp HOME (the registered default
    resolves HOME at import time, so it must be replaced)."""
    plugin = ClaudeCodeSource(projects_root=home / ".claude" / "projects", use_cursor=True)
    monkeypatch.setattr(registry, "_PLUGINS", {"claude_code": plugin})
    return plugin


def _token() -> str:
    return auth.create_grant("host", buckets=list(BUCKETS))


def _count(source: str) -> int:
    return int(get_conn().execute("SELECT COUNT(*) FROM raw_events WHERE source = ?", (source,)).fetchone()[0])


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=30), now + timedelta(days=1)


def _hits(keyword: str, source: str) -> list[dict]:
    token = _token()
    found = tools.search_context(token, [keyword], sources=[source], client=client_of(token))
    assert found["status"] in ("ok", "no_results"), found
    return [row for row in found.get("results", []) if row["source"] == source]


# ── AC1 ──────────────────────────────────────────────────────────────────────────

def test_ac1_only_claude_code_onboards_imports_and_is_searchable(home, claude_plugin, monkeypatch):
    _write_claude_session(home, n=4, keyword="quokkaclaude")
    monkeypatch.setattr(registry, "_PLUGINS", {
        "claude_code": claude_plugin,
        "safari_history": _write_safari(home, keyword="quokkasafari"),  # found, but not chosen
    })

    result = CliRunner().invoke(cli.main, ["install", "onboard", "--sources", "claude_code", "--non-interactive"])

    assert result.exit_code == 0, result.output
    assert "完成" in result.output
    assert disabled_sources() == frozenset({"safari_history"})  # only the chosen source is on
    assert _count("safari_history") == 0
    hits = _hits("quokkaclaude", "claude_code")
    assert hits, "search_context returned no Claude Code hit after onboarding"


def test_interactive_onboarding_asks_each_source_and_applies_answers(home, monkeypatch):
    _write_claude_session(home, n=2, keyword="quokkaclaude")
    monkeypatch.setattr(registry, "_PLUGINS", {
        "claude_code": ClaudeCodeSource(projects_root=home / ".claude" / "projects", use_cursor=True),
        "safari_history": _write_safari(home, keyword="quokkasafari"),
    })

    # One answer per discovered source, in name order: claude_code yes, safari_history no.
    result = CliRunner().invoke(cli.main, ["install", "onboard"], input="y\nn\n")

    assert result.exit_code == 0, result.output
    assert disabled_sources() == frozenset({"safari_history"})
    assert _hits("quokkaclaude", "claude_code")


def test_non_interactive_without_choice_leaves_switches_alone(home, claude_plugin, monkeypatch):
    _write_claude_session(home, n=2, keyword="quokkaclaude")
    monkeypatch.setattr(registry, "_PLUGINS", {"claude_code": claude_plugin})

    result = CliRunner().invoke(cli.main, ["install", "onboard", "--non-interactive"])

    assert result.exit_code == 0, result.output
    assert disabled_sources() == frozenset()
    assert _count("claude_code") == 0  # nothing chosen, nothing imported


# ── AC2 ──────────────────────────────────────────────────────────────────────────

def test_ac2_interrupt_keeps_stored_batches_and_rerun_resumes_from_cursor(home, claude_plugin):
    _write_claude_session(home, n=5, keyword="quokkaresume")
    since, until = _window()

    def stop_after_second_batch(result):
        if result.batches == 2:
            raise RuntimeError("simulated kill after batch 2")

    with pytest.raises(RuntimeError):
        importer.import_source("claude_code", since=since, until=until, batch_size=2,
                               on_batch=stop_after_second_batch)

    progress = importer.read_progress("claude_code")
    assert progress["state"] == "interrupted"
    assert (progress["batches"], progress["events"]) == (2, 4)
    assert _count("claude_code") == 4
    assert _hits("quokkaresume", "claude_code"), "stored batches must be queryable before the import ends"

    # `penpal status` shows the interrupted import.
    status = CliRunner().invoke(cli.main, ["status", "--plain"])
    assert status.exit_code == 0, status.output
    assert "import.claude_code=interrupted,batches=2,events=4,resumed=False" in status.output

    rerun = importer.import_source("claude_code", since=since, until=until, batch_size=2)

    assert rerun.status == "done"
    assert rerun.resumed is True
    assert rerun.events == 1  # only the one event after the saved cursor is read again
    assert _count("claude_code") == 5
    assert get_conn().execute(
        "SELECT COUNT(DISTINCT external_id) FROM raw_events WHERE source = 'claude_code'"
    ).fetchone()[0] == 5
    assert importer.read_progress("claude_code")["state"] == "done"
    status = CliRunner().invoke(cli.main, ["status", "--plain"])
    assert "import.claude_code=done,batches=1,events=1,resumed=True" in status.output


def test_ac2_failure_mid_batch_is_not_double_counted(home, claude_plugin, monkeypatch):
    _write_claude_session(home, n=5, keyword="quokkacrash")
    since, until = _window()
    real_persist = importer.persist_semantic_event
    calls = {"n": 0}

    def disk_fails_on_fifth_event(event, **kwargs):
        calls["n"] += 1
        if calls["n"] == 5:
            raise OSError("simulated disk error")
        return real_persist(event, **kwargs)

    monkeypatch.setattr(importer, "persist_semantic_event", disk_fails_on_fifth_event)
    failed = importer.import_source("claude_code", since=since, until=until, batch_size=2)

    assert failed.status == "failed"
    assert _count("claude_code") == 4
    assert get_cursor(_source_id(claude_plugin))  # cursor kept at the last committed batch

    monkeypatch.setattr(importer, "persist_semantic_event", real_persist)
    rerun = importer.import_source("claude_code", since=since, until=until, batch_size=2)

    assert rerun.status == "done" and rerun.resumed is True and rerun.events == 1
    assert _count("claude_code") == 5

    # Even a full re-read from the start (cursor wiped) adds no rows: the storage unique index blocks repeats.
    get_conn().execute("DELETE FROM sync_cursors")
    get_conn().commit()
    again = importer.import_source("claude_code", since=since, until=until, batch_size=2)
    assert again.status == "done"
    assert _count("claude_code") == 5


def _source_id(plugin) -> int:
    from penpal.store.sync_repo import ensure_source

    instance = plugin.discover()[0]
    return ensure_source(plugin.name, instance.locator)


# ── AC3 ──────────────────────────────────────────────────────────────────────────

def test_ac3_denied_permission_leaves_other_sources_importing(home, claude_plugin, monkeypatch):
    _write_claude_session(home, n=3, keyword="quokkaclaude")
    safari = _write_safari(home, keyword="quokkasafari")
    # The permission probe says Safari history is denied (Full Disk Access not granted).
    monkeypatch.setattr(safari, "availability", lambda: unavailable(PERMISSION_DENIED))
    monkeypatch.setattr(registry, "_PLUGINS", {"claude_code": claude_plugin, "safari_history": safari})

    onboard = CliRunner().invoke(
        cli.main, ["install", "onboard", "--sources", "claude_code,safari_history", "--non-interactive"]
    )

    assert onboard.exit_code == 0, onboard.output
    assert "safari_history" in onboard.output and PERMISSION_DENIED in onboard.output
    assert PANE_FULL_DISK_ACCESS in onboard.output  # the fix path is shown
    assert _count("safari_history") == 0
    assert _hits("quokkaclaude", "claude_code"), "the other source must still import and be searchable"

    doctor = CliRunner().invoke(cli.main, ["doctor", "--plain"])
    assert f"source safari_history: UNAVAILABLE reason={PERMISSION_DENIED}" in doctor.output
    assert PANE_FULL_DISK_ACCESS in doctor.output
    assert "source claude_code:" not in doctor.output  # claude_code is readable, so it is not listed


# ── Historical import gate (fresh install waits for onboarding; existing installs unaffected) ──

def _recording_scheduler(calls: list, *, gate=None):
    from penpal.sources.scheduler import SourcesScheduler

    def fake_read_all(since, until, *, source=None, persist_raw_events=True):
        calls.append(source)
        return iter(())

    kwargs = {} if gate is None else {"history_allowed_fn": gate}
    return SourcesScheduler(enabled=False, list_sources_fn=lambda: [], read_all_fn=fake_read_all, **kwargs)


def test_fresh_home_daemon_does_not_read_history_until_onboarding(home, claude_plugin):
    from penpal.sources.onboarding import apply_choice

    calls: list = []
    scheduler = _recording_scheduler(calls)

    scheduler._scan_plugin("claude_code", "always_interval")
    assert calls == []  # fresh install: the daemon's scheduler does not read history
    assert "history_imports=waiting_onboarding" in CliRunner().invoke(cli.main, ["status", "--plain"]).output

    apply_choice(["claude_code"])  # what onboarding does with the user's choice

    scheduler._scan_plugin("claude_code", "always_interval")
    assert calls == ["claude_code"]
    assert "history_imports=enabled" in CliRunner().invoke(cli.main, ["status", "--plain"]).output


def test_live_capture_rows_do_not_open_the_history_gate(home, claude_plugin):
    from penpal.sources import history_gate
    from penpal.store.models import RawEvent
    from penpal.store.repository import insert_raw_event

    insert_raw_event(RawEvent(source="window", event_type="window_focus", ts_start="2026-10-24T00:00:00+00:00",
                              content_text="live capture row", metadata_json="{}"))

    assert not history_gate.historical_imports_allowed()


def test_existing_install_with_history_is_treated_as_onboarded_once(home, claude_plugin):
    from penpal.sources import history_gate
    from penpal.store.repository import get_state
    from penpal.store.sync_repo import ensure_source

    _write_claude_session(home, n=2, keyword="quokkaexisting")
    ensure_source("claude_code", claude_plugin.discover()[0].locator)  # an earlier import left a source row

    calls: list = []
    _recording_scheduler(calls)._scan_plugin("claude_code", "always_interval")

    assert calls == ["claude_code"]  # no onboarding needed: existing history keeps flowing
    marker = get_state(history_gate.ONBOARDED_KEY)
    assert marker and '"migration"' in marker  # set once by the migration
    assert "history_imports=enabled" in CliRunner().invoke(cli.main, ["status", "--plain"]).output


def test_first_import_has_no_lower_bound_by_default(home, claude_plugin, monkeypatch):
    from penpal.sources import importer, onboarding

    seen: list = []
    real_import = importer.import_source

    def capture_since(name, *, since, until, batch_size=importer.DEFAULT_BATCH_SIZE, on_batch=None):
        seen.append(since)
        return real_import(name, since=since, until=until, batch_size=batch_size, on_batch=on_batch)

    monkeypatch.setattr(importer, "import_source", capture_since)
    _write_claude_session(home, n=2, keyword="quokkaall")

    result = CliRunner().invoke(cli.main, ["install", "onboard", "--sources", "claude_code", "--non-interactive"])

    assert result.exit_code == 0, result.output
    assert onboarding.FIRST_IMPORT_LOOKBACK_DAYS is None
    assert seen == [onboarding.IMPORT_SINCE_FLOOR]


def test_batched_import_drops_user_excluded_domains(home, monkeypatch):
    # #26 (B6) applies to the first-use importer too: a history row on a user-excluded domain is never stored.
    (home / ".penpal" / "config.toml").write_text('[privacy]\nurl_deny_hosts = ["example.org"]\n', encoding="utf-8")
    safari = _write_safari(home, keyword="quokkadenied")  # its only row is https://example.org/doc
    monkeypatch.setattr(registry, "_PLUGINS", {"safari_history": safari})
    since, until = _window()

    importer.import_source("safari_history", since=since, until=until, batch_size=2)

    assert _count("safari_history") == 0
