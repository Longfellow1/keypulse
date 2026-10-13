"""Search feedback (keywords[] per keyword, no_results hint) and the searchable context label.
Synthetic data only. Covers: in_scope / in_results correctness, grant isolation of in_scope, hints,
index mapping, a 5k-row timing check, label at ingest, label backfill (migration v32), and that live
capture window titles are never touched."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_helpers import search
from keypulse.mcp import auth, tools
from keypulse.search.engine import keyword_in_scope, search_events
from keypulse.sources.sink import semantic_event_to_raw_event
from keypulse.sources.types import SemanticEvent
from keypulse.store.context_label import context_label
from keypulse.store.db import close, get_conn, init_db
from keypulse.store.migration_v32 import migrate_v32
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event
from datetime import datetime, timezone

OUT_WORD = "kpfeedbackoutword"  # exists only in a bucket the grant does not cover


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    close()
    init_db(tmp_path / "feedback.db")
    n = [0]

    def put(source, bucket, ts, text, window_title=None, event_type="semantic_event", metadata=None):
        n[0] += 1
        return insert_raw_event(RawEvent(
            source=source, event_type=event_type, ts_start=ts, content_text=text, content_hash=f"fb{n[0]}",
            bucket=bucket, window_title=window_title,
            metadata_json=json.dumps(metadata) if metadata is not None else None))

    yield SimpleNamespace(root=tmp_path, put=put)
    close()


def _ks(resp):
    return {k["index"]: (k["in_scope"], k["in_results"]) for k in resp["keywords"]}


def test_in_scope_and_in_results_per_keyword(db) -> None:
    db.put("claude_code", "Work", "2026-03-01T09:00:00+00:00", "pelican plan and heron notes")
    db.put("claude_code", "Work", "2026-03-02T09:00:00+00:00", "pelican only here")
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, ["pelican", "heron", "nosuchthing"])
    assert resp["status"] == "ok"
    assert _ks(resp) == {0: (True, 2), 1: (True, 1), 2: (False, 0)}


def test_keywords_are_reported_even_when_nothing_is_found(db) -> None:
    db.put("claude_code", "Work", "2026-03-01T09:00:00+00:00", "pelican plan")
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, ["nosuchthing", "otherthing"])
    assert resp["status"] == "no_results"
    assert _ks(resp) == {0: (False, 0), 1: (False, 0)}
    assert resp["hint"] == ["split_or_rephrase"]


def test_out_of_grant_row_never_flips_in_scope(db) -> None:
    db.put("claude_code", "Personal", "2026-03-01T09:00:00+00:00", f"{OUT_WORD} secret row")
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, [OUT_WORD])
    assert resp["status"] == "no_results"
    assert resp["keywords"] == [{"index": 0, "in_scope": False, "in_results": 0}]
    assert resp["hint"] == ["split_or_rephrase"]
    assert keyword_in_scope(OUT_WORD, buckets=["Work"], event_type="semantic_event") is False
    assert keyword_in_scope(OUT_WORD, buckets=["Personal"], event_type="semantic_event") is True


def test_out_of_grant_source_never_flips_in_scope(db) -> None:
    db.put("git_log", "Work", "2026-03-01T09:00:00+00:00", f"{OUT_WORD} commit")
    token = auth.create_grant("host", buckets=["Work"], sources=["claude_code"])
    resp = search(token, [OUT_WORD])
    assert resp["keywords"][0]["in_scope"] is False
    assert resp["hint"] == ["split_or_rephrase"]


def test_requested_source_outside_grant_is_not_in_scope(db) -> None:
    db.put("git_log", "Work", "2026-03-01T09:00:00+00:00", "pelican commit")
    token = auth.create_grant("host", buckets=["Work"], sources=["claude_code"])
    resp = search(token, ["pelican"], sources=["git_log"])
    assert resp["keywords"] == [{"index": 0, "in_scope": False, "in_results": 0}]


def test_time_range_limits_in_scope(db) -> None:
    db.put("claude_code", "Work", "2026-03-01T09:00:00+00:00", "pelican in march")
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, ["pelican"], {"start": "2026-06-01", "end": "2026-06-30"})
    assert resp["keywords"] == [{"index": 0, "in_scope": False, "in_results": 0}]


def test_hint_browse_timeline_when_range_has_events_for_a_source(db) -> None:
    db.put("claude_code", "Work", "2026-03-01T09:00:00+00:00", "pelican in march")
    db.put("claude_code", "Work", "2026-06-10T09:00:00+00:00", "unrelated june chat")
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, ["pelican"], {"start": "2026-06-01", "end": "2026-06-30"})
    assert resp["status"] == "no_results"
    assert resp["hint"] == ["split_or_rephrase", "browse_timeline"]


def test_hint_browse_timeline_only_for_requested_sources(db) -> None:
    db.put("claude_code", "Work", "2026-06-10T09:00:00+00:00", "unrelated june chat")
    db.put("git_log", "Work", "2026-03-01T09:00:00+00:00", "old commit")
    token = auth.create_grant("host", buckets=["Work"])
    # git_log has nothing in June; the requested source is git_log, so no browse hint.
    resp = search(token, ["pelican"], {"start": "2026-06-01", "end": "2026-06-30"}, sources=["git_log"])
    assert resp["hint"] == ["split_or_rephrase"]


def test_no_browse_hint_without_time_range(db) -> None:
    db.put("claude_code", "Work", "2026-06-10T09:00:00+00:00", "unrelated june chat")
    token = auth.create_grant("host", buckets=["Work"])
    assert search(token, ["pelican"])["hint"] == ["split_or_rephrase"]


def test_no_split_hint_when_a_keyword_is_in_scope() -> None:
    feedback = [{"index": 0, "in_scope": False, "in_results": 0}, {"index": 1, "in_scope": True, "in_results": 0}]
    coverage = {"by_source": [{"source": "claude_code", "earliest": None, "latest": None, "has_events_in_range": False}]}
    assert tools._no_results_hints(feedback, False, None, coverage) == []
    assert tools._no_results_hints(feedback[:1], False, None, coverage) == ["split_or_rephrase"]


def test_keyword_index_points_at_callers_first_occurrence(db) -> None:
    db.put("claude_code", "Work", "2026-03-01T09:00:00+00:00", "pelican plan")
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, ["zzz", " Pelican ", "pelican"])
    assert [k["index"] for k in resp["keywords"]] == [0, 1]
    assert _ks(resp)[1] == (True, 1)


def test_keyword_text_is_never_echoed(db) -> None:
    db.put("claude_code", "Work", "2026-03-01T09:00:00+00:00", "pelican plan")
    token = auth.create_grant("host", buckets=["Work"])
    raw = json.dumps(search(token, ["zzzcanaryword", "pelican"]))
    assert "zzzcanaryword" not in raw


def test_keyword_check_on_5k_rows_is_fast(db) -> None:
    for i in range(5000):
        db.put("claude_code", "Work", f"2026-03-{1 + i % 28:02d}T09:00:00+00:00", f"synthetic note {i} filler text")
    db.put("claude_code", "Work", "2026-03-02T09:00:00+00:00", "needle here")
    token = auth.create_grant("host", buckets=["Work"])
    keywords = ["needle", "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota"]
    start = time.perf_counter()
    resp = search(token, keywords)
    elapsed = time.perf_counter() - start
    assert resp["status"] == "ok"
    assert _ks(resp)[0] == (True, 1)
    assert elapsed < 2.0, f"search with per-keyword feedback took {elapsed:.2f}s on 5k rows"


# ── searchable context label ────────────────────────────────────────────────────────────────
def test_context_label_per_source() -> None:
    assert context_label("git_log", {"repo_name": "keypulse"}) == "keypulse"
    assert context_label("codex_cli", {"project": "demo-app"}) == "demo-app"
    assert context_label("codex_cli", {"cwd": "/work/demo-app"}) == "demo-app"
    assert context_label("codex_cli", {"session_id": "s1"}) is None
    assert context_label("claude_code", {"project_dir": "/Users/x/.claude/projects/-Users-x-Go-keypulse"}) == \
        "Go-keypulse"  # account path dropped
    assert context_label("claude_code", {"project_dir": "/p/-opt-tools"}) == "-opt-tools"
    assert context_label("chrome_history", {"artifact": "https://user:pw@docs.example.com:8443/a/b?q=1"}) == \
        "docs.example.com"
    assert context_label("safari_history", {"artifact": "not a url"}) is None
    assert context_label("markdown_vault", {"artifact": "notes/ops/deploy.md"}) == "notes/ops/deploy"
    assert context_label("markdown_vault", {"artifact": "/abs/x.md"}) is None
    assert context_label("browser_url", {"artifact": "https://example.com"}) is None
    assert context_label("git_log", {"repo_name": "   "}) is None


def test_ingest_sets_label_for_imported_rows(db) -> None:
    event = SemanticEvent(
        time=datetime(2026, 3, 1, 9, tzinfo=timezone.utc), source="git_log", actor="me", intent="fix typo",
        artifact="commit:abc1234", raw_ref="git:keypulse:abc1234", privacy_tier="work",
        metadata={"repo_name": "keypulse", "full_hash": "abc1234"})
    assert semantic_event_to_raw_event(event).window_title == "keypulse"


def test_repo_name_is_searchable_after_ingest(db) -> None:
    event = SemanticEvent(
        time=datetime(2026, 3, 1, 9, tzinfo=timezone.utc), source="git_log", actor="me", intent="refactor parser",
        artifact="commit:abc1234", raw_ref="git:zephyrrepo:abc1234", privacy_tier="work",
        metadata={"repo_name": "zephyrrepo"})
    insert_raw_event(semantic_event_to_raw_event(event))
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, ["zephyrrepo"])
    assert resp["status"] == "ok"
    assert resp["results"][0]["source"] == "git_log"
    assert resp["keywords"] == [{"index": 0, "in_scope": True, "in_results": 1}]


def test_label_does_not_change_fold_of_identical_text(db) -> None:
    db.put("git_log", "Work", "2026-03-01T09:00:00+00:00", "fix typo", window_title="repo-a")
    db.put("git_log", "Work", "2026-03-02T09:00:00+00:00", "fix typo", window_title="repo-b")
    hits = search_events(["typo"], buckets=["Work"])
    assert len(hits) == 1 and hits[0]["occurrences"] == 2


def test_migration_v32_labels_old_rows_idempotently_and_fts_finds_them(db) -> None:
    old = db.put("git_log", "Work", "2026-03-01T09:00:00+00:00", "old commit text",
                 metadata={"repo_name": "quasarrepo"})
    site = db.put("chrome_history", "Work", "2026-03-01T10:00:00+00:00", "a page", metadata={"artifact": "https://docs.quasar.example/path?x=1"})
    live = db.put("browser_url", "Work", "2026-03-01T11:00:00+00:00", "live title", window_title="Live Title - Chrome",
                  event_type="browser_url_capture", metadata={"url": "https://quasar.example"})
    capture = db.put("claude_code", "Work", "2026-03-01T12:00:00+00:00", "capture row", window_title="Kept Title",
                     event_type="window_focus_session", metadata={"project_dir": "/x/quasarrepo"})
    first = migrate_v32(get_conn())
    get_conn().commit()
    assert first["git_log"] == (1, 0) and first["chrome_history"] == (1, 0)
    assert migrate_v32(get_conn()) == {}  # second run: nothing left to label
    get_conn().commit()
    titles = dict(get_conn().execute("SELECT id, window_title FROM raw_events").fetchall())
    assert titles[old] == "quasarrepo"
    assert titles[site] == "docs.quasar.example"
    assert titles[live] == "Live Title - Chrome"
    assert titles[capture] == "Kept Title"
    token = auth.create_grant("host", buckets=["Work"])
    assert [r["id"] for r in search(token, ["quasarrepo"])["results"]] == [f"evt_{old}"]
    assert search(token, ["docs.quasar"])["status"] == "ok"


def test_migration_v32_second_run_writes_nothing(db) -> None:
    db.put("git_log", "Work", "2026-03-01T09:00:00+00:00", "commit", metadata={"repo_name": "idemrepo"})
    migrate_v32(get_conn())
    get_conn().commit()
    before = get_conn().total_changes
    migrate_v32(get_conn())
    assert get_conn().total_changes == before
