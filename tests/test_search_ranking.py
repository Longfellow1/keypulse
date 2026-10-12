"""M2-1: near-identical results collapse into one hit; specificity decides ties. Synthetic data only."""
from __future__ import annotations

import json
from itertools import count

import pytest

from keypulse.mcp import auth
from keypulse.search.engine import _is_rare_token, search_events
from keypulse.store.db import close, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event
from keypulse.store.sync_repo import add_evidence
from mcp_helpers import search

_n = count(1)


def _ins(text, ts, *, source="claude_code", bucket="Work", title=None, app=None, event_type="semantic_event") -> int:
    rid = insert_raw_event(RawEvent(
        source=source, event_type=event_type, ts_start=ts, content_text=text, window_title=title, app_name=app,
        content_hash=f"h{next(_n)}", bucket=bucket, semantic_weight=0.5,
        metadata_json=json.dumps({"session_id": "s"}),
    ))
    add_evidence(rid, f"/synthetic/{source}/{rid}.jsonl", 1, None)
    return rid


@pytest.fixture
def db(tmp_path):
    close()
    init_db(tmp_path / "k.db")
    yield
    close()


# ── collapse ─────────────────────────────────────────────────────────────────────────────────
def test_identical_rows_collapse_with_count_and_range(db) -> None:
    ids = [_ins("pelican dashboard", f"2026-05-0{d}T10:00:00+00:00", source="chrome_history") for d in (1, 2, 3)]
    other = _ins("pelican runbook", "2026-05-04T10:00:00+00:00", source="chrome_history")
    hits = search_events(["pelican"], buckets=["Work"])
    assert len(hits) == 2
    folded = next(h for h in hits if h["id"] != other)
    assert folded["occurrences"] == 3
    assert folded["first_seen"].startswith("2026-05-01") and folded["last_seen"].startswith("2026-05-03")
    assert folded["id"] in ids and folded["evidence_id"] is not None  # the representative keeps its own evidence
    assert next(h for h in hits if h["id"] == other)["occurrences"] == 1


def test_collapse_runs_before_limit_so_repeats_do_not_crowd(db) -> None:
    for d in range(1, 8):
        _ins("pelican dashboard", f"2026-05-{d:02d}T10:00:00+00:00", source="chrome_history")
    distinct = _ins("pelican export guide", "2026-04-01T10:00:00+00:00", source="chrome_history")
    top2 = search_events(["pelican"], buckets=["Work"], limit=2)
    assert len(top2) == 2 and distinct in {h["id"] for h in top2}


def test_different_source_or_bucket_never_merge(db) -> None:
    _ins("pelican plan", "2026-05-01T10:00:00+00:00", source="git_log")
    _ins("pelican plan", "2026-05-02T10:00:00+00:00", source="zsh_history")
    _ins("pelican plan", "2026-05-03T10:00:00+00:00", source="zsh_history", bucket="Personal")
    hits = search_events(["pelican"], buckets=["Work", "Personal"])
    assert len(hits) == 3 and all(h["occurrences"] == 1 for h in hits)
    # browser sources are one family: chrome + safari with the same title in one bucket fold
    _ins("pelican wiki", "2026-05-01T10:00:00+00:00", source="chrome_history")
    _ins("pelican wiki", "2026-05-02T10:00:00+00:00", source="safari_history")
    wiki = [h for h in search_events(["wiki"], buckets=["Work"])]
    assert len(wiki) == 1 and wiki[0]["occurrences"] == 2


def test_collapse_cannot_see_or_count_out_of_scope_rows(db) -> None:
    for d in (1, 2):
        _ins("pelican secret plan", f"2026-05-0{d}T10:00:00+00:00", bucket="Work")
    for d in (3, 4, 5):
        _ins("pelican secret plan", f"2026-05-0{d}T10:00:00+00:00", bucket="Financial")
    work = search_events(["pelican"], buckets=["Work"])
    assert len(work) == 1 and work[0]["occurrences"] == 2
    assert work[0]["last_seen"].startswith("2026-05-02")  # the Financial rows (3rd-5th) leave no trace
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, ["pelican"])
    assert [r["occurrences"] for r in resp["results"]] == [2]
    assert resp["results"][0]["last_seen"].startswith("2026-05-02")
    assert "Financial" not in json.dumps(resp)
    sourced = auth.create_grant("host2", buckets=["Work", "Financial"], sources=["claude_code"])
    assert search(sourced, ["pelican"], sources=["git_log"])["status"] == "no_results"


def test_window_sessions_fold_on_app_and_title_not_duration(db) -> None:
    for h in (1, 2, 3):
        _ins("", f"2026-05-01T0{h}:00:00+00:00", source="window", app="Cursor", title="ops.md - keypulse",
             event_type="window_focus_session")
    hits = search_events(["ops.md"], buckets=["Work"], event_type="window_focus_session")
    assert len(hits) == 1 and hits[0]["occurrences"] == 3


# ── specificity ──────────────────────────────────────────────────────────────────────────────
def test_more_distinct_keywords_beats_longer_and_more_frequent(db) -> None:
    long_frequent = _ins("pelican " * 30 + "notes about the pelican rollout", "2026-05-02T10:00:00+00:00")
    covers_both = _ins("pelican rollout checklist", "2026-05-01T10:00:00+00:00")
    ids = [h["id"] for h in search_events(["pelican", "rollout"], buckets=["Work"])]
    assert ids[0] == covers_both and ids[1] == long_frequent


def test_focused_short_row_beats_long_transcript_message(db) -> None:
    rambling = _ins(
        "the export failed, the export failed again, retried the export, the export failed with quota mention " * 3,
        "2026-05-02T10:00:00+00:00",
    )
    focused = _ins("export failed because the quota was exceeded", "2026-05-01T10:00:00+00:00")
    ids = [h["id"] for h in search_events(["export", "failed", "quota"], buckets=["Work"])]
    assert ids == [focused, rambling]


def test_exact_rare_token_beats_substring_hit(db) -> None:
    substring = _ins("build abc1234567 finished on dev", "2026-05-02T10:00:00+00:00")
    exact = _ins("build abc1234 finished on dev", "2026-05-01T10:00:00+00:00")
    ids = [h["id"] for h in search_events(["abc1234", "build"], buckets=["Work"])]
    assert ids == [exact, substring]


def test_rare_token_detection() -> None:
    for t in ("7e41c9d", "v1.2.3", "v2.4", "retry_budget.py", "KP-482", "a93bb10"):
        assert _is_rare_token(t), t
    for t in ("deploy", "dev", "feature flag", "abc", "deadbeef"):  # words / hex-lookalike words without digits
        assert not _is_rare_token(t), t


def test_copy_cue_ranks_clipboard_rows_first_and_ties_are_deterministic(db) -> None:
    _ins("push to dev finished, commit deployed", "2026-05-06T09:00:00+00:00", source="codex_cli")
    _ins("push to dev, smoke test green", "2026-05-06T10:00:00+00:00", source="git_log")
    clip = _ins("7e41c9d..a93bb10  dev -> dev", "2026-05-06T11:00:00+00:00", source="clipboard",
                event_type="clipboard_copy")
    kw = dict(buckets=["Work"], event_type=["semantic_event", "clipboard_copy"])
    first = [h["id"] for h in search_events(["push", "dev", "copied"], **kw)]
    assert first[0] == clip
    assert first == [h["id"] for h in search_events(["push", "dev", "copied"], **kw)]
    # without the cue the clipboard row only matches one keyword and does not jump the queue
    assert [h["id"] for h in search_events(["push", "dev"], **kw)][0] != clip


def test_identical_rows_order_is_newest_first(db) -> None:
    a = _ins("pelican alpha", "2026-05-01T10:00:00+00:00", source="git_log")
    b = _ins("pelican beta", "2026-05-02T10:00:00+00:00", source="git_log")
    ids = [h["id"] for h in search_events(["pelican"], buckets=["Work"])]
    assert ids == [b, a]
