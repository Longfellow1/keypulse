"""P0-11a (D26): Penpal's own capture streams are queryable by granted hosts.

Covers the app category preset, bucket resolution order, write-time secret redaction, searchable
set + snippets, penpal evidence, the v23 backfill (idempotent, fast), and bucket scoping of
capture rows end to end. Synthetic data only.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from penpal.capture.normalizer import (
    normalize_ax_text_event,
    normalize_browser_url_event,
    normalize_clipboard_event,
    normalize_idle_event,
    normalize_keyboard_chunk_event,
    normalize_window_event,
)
from penpal.config import Config
from penpal.mcp import auth, tools
from penpal.store import buckets
from penpal.store.app_categories import classify_app
from penpal.store.capture_events import is_keypulse_locator, keypulse_locator
from penpal.store.db import close, get_conn, init_db
from penpal.store.migration_v23 import migrate_v23
from penpal.store.migrations import MIGRATIONS, run_migrations
from penpal.store.models import Policy
from penpal.store.repository import insert_policy, insert_raw_event
from mcp_helpers import search

FAKE_KEY = "sk-test-FAKE0000000000000000000000"


def test_new_capture_locator_uses_penpal_prefix_and_reads_legacy_prefix():
    assert keypulse_locator(42) == "penpal://event/42"
    assert is_keypulse_locator("penpal://event/42")
    assert is_keypulse_locator("keypulse://event/42")
    assert not is_keypulse_locator("other://event/42")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def db(tmp_path):
    buckets.reset_cache()
    init_db(tmp_path / "kp.db")
    yield tmp_path
    close()
    buckets.reset_cache()


def _bucket(row_id):
    return get_conn().execute("SELECT bucket FROM raw_events WHERE id = ?", (row_id,)).fetchone()[0]


def _timeline(token, start, end, **kw):
    return tools.get_timeline(token, start, end, client="h", **kw)


def _grant(*bs, **kw):
    return auth.create_grant("h", buckets=list(bs), **kw)


# ── preset table ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("app,expected", [
    ("Visual Studio Code", "Work"), ("Code", "Work"), ("Cursor", "Work"), ("Xcode", "Work"), ("PyCharm", "Work"),
    ("iTerm2", "Work"), ("Terminal", "Work"), ("Warp", "Work"), ("Ghostty", "Work"),
    ("Claude", "Work"), ("ChatGPT", "Work"), ("LM Studio", "Work"), ("Ollama", "Work"),
    ("Obsidian", "Work"), ("Notion", "Work"), ("Microsoft Excel", "Work"), ("Keynote", "Work"),
    ("飞书", "Work"), ("Lark", "Work"), ("钉钉", "Work"), ("Slack", "Work"), ("zoom.us", "Work"), ("Figma", "Work"),
    ("支付宝", "Financial"), ("Alipay", "Financial"), ("PayPal", "Financial"), ("招商银行", "Financial"),
    ("Bank of America", "Financial"), ("Robinhood", "Financial"), ("同花顺", "Financial"), ("东方财富", "Financial"),
    ("Google Chrome", "Personal"), ("Safari", "Personal"), ("Spotify", "Personal"), ("Barcode Scanner", "Personal"),
    ("Mail", "Personal"), ("", "Personal"), (None, "Personal"),
])
def test_preset_categories(app, expected):
    assert (classify_app(app) or "Personal") == expected


def test_preset_matches_process_name_and_financial_wins():
    assert classify_app(None, "com.googlecode.iterm2") == "Work"
    assert classify_app("Electron", "Visual Studio Code") == "Work"
    assert classify_app("Slack Bank Alerts", None) == "Financial"  # Financial is checked before Work


# ── resolution order: user rule > config override > preset > Personal ────────────────────────
def test_resolution_order(db, monkeypatch):
    ev = lambda app, n: insert_raw_event(normalize_keyboard_chunk_event(f"text {n}", app_name=app, ts_start=f"2026-03-01T09:0{n}:00+00:00"))
    assert _bucket(ev("Cursor", 1)) == "Work"                 # preset
    assert _bucket(ev("Spotify", 2)) == "Personal"            # unknown app
    assert _bucket(ev("Alipay", 3)) == "Financial"            # preset

    monkeypatch.setattr(Config, "load", classmethod(lambda cls: Config(buckets={"app_defaults": {"cursor": "Personal", "Spotify": "Work"}})))
    buckets.reset_cache()
    assert _bucket(ev("Cursor", 4)) == "Personal"             # config beats preset
    assert _bucket(ev("Spotify", 5)) == "Work"
    insert_policy(Policy(scope_type="app", scope_value="Cursor", mode=buckets.BUCKET_MODE,
                         config_json=json.dumps({"bucket": "Private"}), priority=10))
    assert _bucket(ev("Cursor", 6)) == "Private"              # user rule beats config


def test_plugin_sources_and_unmapped_sources_are_unchanged(db):
    mk = lambda src, n: insert_raw_event(__import__("penpal.store.models", fromlist=["RawEvent"]).RawEvent(
        source=src, event_type="semantic_event", ts_start=f"2026-03-01T09:0{n}:00+00:00", content_text="x", content_hash=f"p{n}", app_name="Cursor"))
    assert _bucket(mk("chrome_history", 1)) == "Personal"     # source default beats app preset
    assert _bucket(mk("mystery", 2)) is None                  # still never auto-placed
    assert _bucket(mk("spotlight", 3)) == "Personal"


# ── write-time redaction ─────────────────────────────────────────────────────────────────────
def test_capture_write_redacts_secrets_in_text_title_and_fts(db):
    rid = insert_raw_event(normalize_keyboard_chunk_event(
        f"export key then api_key={FAKE_KEY} is mine, renew in march", app_name="Cursor", window_title=f"token={FAKE_KEY}",
        ts_start="2026-03-01T09:00:00+00:00"))
    row = get_conn().execute("SELECT content_text, window_title FROM raw_events WHERE id = ?", (rid,)).fetchone()
    assert FAKE_KEY not in row["content_text"] and FAKE_KEY not in (row["window_title"] or "")
    assert "renew in march" in row["content_text"]
    assert get_conn().execute("SELECT count(*) FROM raw_events_fts WHERE raw_events_fts MATCH ?", ('"FAKE0000"',)).fetchone()[0] == 0


# ── searchable set, snippets, evidence ───────────────────────────────────────────────────────
def _seed_capture():
    ts = lambda m: f"2026-03-02T10:{m:02d}:00+00:00"
    ids = {}
    ids["kb"] = insert_raw_event(normalize_keyboard_chunk_event("drafting the quarterly zebracorn roadmap", app_name="Cursor", ts_start=ts(1)))
    ids["clip"] = insert_raw_event(normalize_clipboard_event("zebracorn invoice number 4471", app_name="Safari", ts_start=ts(2)))
    ids["ax"] = insert_raw_event(normalize_ax_text_event("zebracorn dashboard shows 12 open items", app_name="Notion", ts_start=ts(3)))
    ids["url"] = insert_raw_event(normalize_browser_url_event("https://example.com/zebracorn", "Zebracorn field guide", browser_name="Safari", ts_start=ts(4)))
    ids["win"] = insert_raw_event(normalize_window_event(
        "window_focus_session", "Figma", "Zebracorn mockups", None, ts_start=ts(5), ts_end="2026-03-02T11:12:00+00:00"))
    ids["idle"] = insert_raw_event(normalize_idle_event("idle_start", 300, ts_start=ts(6)))
    return ids


def test_capture_streams_are_searchable_with_kind_and_snippet(db):
    ids = _seed_capture()
    token = _grant("Work", "Personal")
    resp = search(token, ["zebracorn"], limit=20)
    by_source = {r["source"]: r for r in resp["results"]}
    assert set(by_source) == {"keyboard_chunk", "clipboard", "ax_text", "browser_url", "window"}   # idle never
    assert by_source["keyboard_chunk"]["bucket"] == "Work" and "[Cursor]" in by_source["keyboard_chunk"]["snippet"]
    assert by_source["clipboard"]["bucket"] == "Personal" and "4471" in by_source["clipboard"]["snippet"]
    assert by_source["window"]["bucket"] == "Work"
    assert "Figma" in by_source["window"]["snippet"] and "Zebracorn mockups" in by_source["window"]["snippet"]
    assert "1h07m" in by_source["window"]["snippet"]   # duration from ts_start..ts_end
    assert all(r["evidence_id"] and r["evidence_available"] for r in resp["results"])
    assert not any(r["id"] == f"evt_{ids['idle']}" for r in resp["results"])


def test_keyword_can_hit_the_app_name(db):
    _seed_capture()
    token = _grant("Work")
    resp = search(token, ["Figma"])
    assert [r["source"] for r in resp["results"]] == ["window"]
    assert "Figma" in resp["results"][0]["snippet"]


def test_capture_evidence_points_at_keypulse_and_is_always_available(db):
    ids = _seed_capture()
    token = _grant("Work")
    hit = search(token, ["quarterly"])["results"][0]
    ev = tools.get_evidence(token, hit["evidence_id"], client="h")
    assert ev["status"] == "ok"
    assert ev["evidence"]["locator"] == {"type": "penpal", "event_id": f"evt_{ids['kb']}"}
    assert ev["evidence"]["available"] is True and "zebracorn roadmap" in ev["evidence"]["excerpt"]
    win = tools.get_evidence(token, search(token, ["Figma"])["results"][0]["evidence_id"], client="h")
    assert "Zebracorn mockups" in win["evidence"]["excerpt"] and "1h07m" in win["evidence"]["excerpt"]


def test_timeline_includes_capture_rows_in_order(db):
    _seed_capture()
    token = _grant("Work", "Personal")
    resp = _timeline(token, "2026-03-02", "2026-03-02")
    assert [r["source"] for r in resp["results"]] == ["keyboard_chunk", "clipboard", "ax_text", "browser_url", "window"]
    assert "Figma" in resp["results"][-1]["snippet"]


# ── scoping: capture rows obey buckets and sources ──────────────────────────────────────────
def test_capture_rows_in_other_buckets_are_invisible_end_to_end(db):
    insert_raw_event(normalize_keyboard_chunk_event("transfer zebracorn wire to account", app_name="支付宝", ts_start="2026-03-02T10:00:00+00:00"))
    insert_raw_event(normalize_clipboard_event("zebracorn grocery list", app_name="Notes", ts_start="2026-03-02T10:01:00+00:00"))
    insert_raw_event(normalize_keyboard_chunk_event("zebracorn unit test", app_name="Cursor", ts_start="2026-03-02T10:02:00+00:00"))
    work = search(_grant("Work"), ["zebracorn"])
    assert [r["snippet"] for r in work["results"]] == ["[Cursor] zebracorn unit test"]
    assert "transfer" not in json.dumps(work) and "grocery" not in json.dumps(work)
    assert search(_grant("Work", sources=["clipboard"]), ["zebracorn"])["status"] == "no_results"
    fin = search(_grant("Financial"), ["zebracorn"])
    assert [r["bucket"] for r in fin["results"]] == ["Financial"]


# ── migration v23 ────────────────────────────────────────────────────────────────────────────
def _db_at_v22(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE _schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)")
    run_migrations_to = 22
    for i, step in enumerate(MIGRATIONS[:run_migrations_to], start=1):
        if callable(step):
            step(conn)
        else:
            conn.executescript(step.strip())
        conn.execute("INSERT INTO _schema_version VALUES (?, 'x')", (i,))
    conn.commit()
    return conn


def _legacy_row(conn, n, source, event_type, app, text, title=None, bucket=None):
    conn.execute(
        "INSERT INTO raw_events(source,event_type,ts_start,app_name,window_title,content_text,content_hash,created_at,bucket) "
        "VALUES (?,?,?,?,?,?,?,'2026-01-01',?)",
        (source, event_type, f"2026-01-01T00:00:{n % 60:02d}+00:00#{n}", app, title, text, f"h{n}", bucket))


def test_v23_backfill_classifies_redacts_adds_evidence_and_is_idempotent(tmp_path):
    conn = _db_at_v22(tmp_path / "old.db")
    _legacy_row(conn, 1, "keyboard_chunk", "keyboard_chunk", "Cursor", f"note api_key={FAKE_KEY} zebracorn")
    _legacy_row(conn, 2, "clipboard", "clipboard_copy", "Alipay", "balance zebracorn")
    _legacy_row(conn, 3, "window", "window_focus_session", "Safari", None, title=f"x?token={FAKE_KEY[:20]}zzzzzzzz page")
    _legacy_row(conn, 4, "ax_text", "ax_text_capture", "Cursor", "kept while editing", bucket="Private")    # user's choice survives
    _legacy_row(conn, 5, "idle", "idle_start", None, None)
    _legacy_row(conn, 6, "claude_code", "semantic_event", "Cursor", "plugin row")             # not a capture source
    conn.execute("INSERT INTO policies(scope_type,scope_value,mode,enabled,priority,config_json,created_at,updated_at) "
                 "VALUES ('app','Alipay','bucket',1,1,'{\"bucket\": \"Private\"}','x','x')")
    conn.commit()

    run_migrations(conn)
    rows = {r["content_hash"]: r for r in conn.execute("SELECT * FROM raw_events")}
    assert rows["h1"]["bucket"] == "Work" and rows["h3"]["bucket"] == "Personal"
    assert rows["h2"]["bucket"] == "Private"                  # user rule beat the preset (Financial)
    assert rows["h4"]["bucket"] == "Private"                  # existing bucket never overwritten
    assert rows["h5"]["bucket"] == "Personal"
    assert rows["h6"]["bucket"] is None  # plugin source: not app-classified
    assert FAKE_KEY not in rows["h1"]["content_text"] and "zebracorn" in rows["h1"]["content_text"]
    assert "zzzzzzzz" not in rows["h3"]["window_title"]
    fts = lambda q: conn.execute("SELECT rowid FROM raw_events_fts WHERE raw_events_fts MATCH ?", (q,)).fetchall()
    assert [r[0] for r in fts('"zebracorn"')] == [rows["h1"]["id"], rows["h2"]["id"]]   # index follows the update
    assert fts('"FAKE0000"') == []
    ev = {r["event_id"]: r["locator"] for r in conn.execute("SELECT event_id, locator FROM evidence")}
    assert ev[rows["h1"]["id"]] == f"penpal://event/{rows['h1']['id']}"
    assert rows["h5"]["id"] not in ev and rows["h6"]["id"] not in ev    # idle / plugin rows get none

    before = [tuple(r) for r in conn.execute("SELECT * FROM raw_events ORDER BY id")]
    before_ev = conn.execute("SELECT count(*) FROM evidence").fetchone()[0]
    migrate_v23(conn)                                          # rerun is a no-op
    assert [tuple(r) for r in conn.execute("SELECT * FROM raw_events ORDER BY id")] == before
    assert conn.execute("SELECT count(*) FROM evidence").fetchone()[0] == before_ev
    conn.close()


def test_v23_backfill_is_fast_on_25k_rows_and_atomic(tmp_path, capsys):
    conn = _db_at_v22(tmp_path / "big.db")
    apps = ["Cursor", "Safari", "Alipay", "Slack", "Spotify", "Preview", "Notes", "Figma", "读书笔记", "Chrome"]
    conn.executemany(
        "INSERT INTO raw_events(source,event_type,ts_start,app_name,window_title,content_text,content_hash,created_at) "
        "VALUES (?,?,?,?,?,?,?,'2026-01-01')",
        [("keyboard_chunk", "keyboard_chunk", f"2026-01-01T00:00:00+00:00#{i}", apps[i % 10], "window title %d" % (i % 50),
          ("typed some ordinary words about project %d and lunch plans " % i) * 3 + (f"api_key={FAKE_KEY}" if i % 100 == 0 else ""), f"h{i}")
         for i in range(25_000)])
    conn.commit()
    start = time.perf_counter()
    run_migrations(conn)
    elapsed = time.perf_counter() - start
    print(f"v23 backfill 25k rows: {elapsed:.2f}s")
    assert elapsed < 30
    assert conn.execute("SELECT count(*) FROM raw_events WHERE bucket IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM raw_events WHERE content_text LIKE '%FAKE0000%'").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM evidence").fetchone()[0] == 25_000
    conn.close()
