"""Cross-source folding of browser pages, superseded progressive-typing snapshots, migration v33.
Synthetic data only."""
from __future__ import annotations

import json
from itertools import count

import pytest

from keypulse.search.engine import scoped_evidence, search_events, timeline_events
from keypulse.store.db import close, get_conn, init_db
from keypulse.store.migration_v33 import migrate_v33
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event
from keypulse.store.sync_repo import add_evidence

_n = count(1)
ALL_TYPES = ("semantic_event", "ax_text_capture", "window_focus_session", "browser_url_capture",
             "keyboard_chunk", "clipboard_copy")


def _ins(text, ts, *, source="chrome_history", bucket="Work", title=None, app=None,
         event_type="semantic_event") -> int:
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


def _search(keywords, **kw):
    kw.setdefault("event_type", ALL_TYPES)
    return search_events(keywords, **kw)


# ── cross-source fold ──────────────────────────────────────────────────────────────────────────
def test_same_page_from_four_sources_is_one_hit_with_also_seen_in(db) -> None:
    _ins("Pelican Guide - Quarterly", "2026-05-01T10:00:00+00:00", source="chrome_history")
    _ins("Pelican Guide - Quarterly", "2026-05-01T10:01:00+00:00", source="safari_history")
    _ins("Pelican Guide - Quarterly", "2026-05-01T10:02:00+00:00", source="browser_url",
         event_type="browser_url_capture", app="Google Chrome",
         title="Pelican Guide - Quarterly - Google Chrome")
    _ins(None, "2026-05-01T10:03:00+00:00", source="window", event_type="window_focus_session",
         app="Google Chrome", title="(3) Pelican Guide - Quarterly - Google Chrome")
    hits = _search(["pelican"], buckets=["Work"])
    assert len(hits) == 1
    hit = hits[0]
    assert hit["occurrences"] == 4
    everything = {"chrome_history", "safari_history", "browser_url", "window"}
    assert hit["source"] in everything
    assert hit["also_seen_in"] == sorted(everything - {hit["source"]})
    assert hit["evidence_id"] is not None  # representative keeps its own evidence


def test_same_title_two_hours_apart_stays_two_hits(db) -> None:
    _ins("Pelican Guide", "2026-05-01T10:00:00+00:00", source="chrome_history")
    _ins("Pelican Guide", "2026-05-01T12:00:00+00:00", source="chrome_history")
    hits = _search(["pelican"], buckets=["Work"])
    assert len(hits) == 2 and all(h["occurrences"] == 1 and h["also_seen_in"] == [] for h in hits)


def test_page_visits_chain_within_ten_minutes_and_split_after(db) -> None:
    # 0, 6, 12 min: each gap is within 10 min, so one run; the 40 min visit starts a new hit.
    for minute in (0, 6, 12, 40):
        _ins("Pelican Guide", f"2026-05-01T10:{minute:02d}:00+00:00", source="safari_history")
    hits = _search(["pelican"], buckets=["Work"])
    assert sorted(h["occurrences"] for h in hits) == [1, 3]
    folded = next(h for h in hits if h["occurrences"] == 3)
    assert folded["first_seen"].startswith("2026-05-01T10:00") and folded["last_seen"].startswith("2026-05-01T10:12")


def test_different_bucket_never_folds(db) -> None:
    _ins("Pelican Guide", "2026-05-01T10:00:00+00:00", source="chrome_history", bucket="Work")
    _ins("Pelican Guide", "2026-05-01T10:00:00+00:00", source="safari_history", bucket="Personal")
    hits = _search(["pelican"], buckets=["Work", "Personal"])
    assert len(hits) == 2 and all(h["also_seen_in"] == [] for h in hits)


def test_out_of_grant_row_never_appears_in_also_seen_in(db) -> None:
    _ins("Pelican Guide", "2026-05-01T10:00:00+00:00", source="chrome_history", bucket="Work")
    _ins("Pelican Guide", "2026-05-01T10:01:00+00:00", source="safari_history", bucket="Personal")
    work = _search(["pelican"], buckets=["Work"])
    assert len(work) == 1 and work[0]["occurrences"] == 1 and work[0]["also_seen_in"] == []
    # other bucket: never folded with the Work row, in either direction
    both = _search(["pelican"], buckets=["Work", "Personal"])
    assert len(both) == 2 and all(h["also_seen_in"] == [] and h["occurrences"] == 1 for h in both)
    # same bucket, but the grant only allows chrome_history: the safari row is neither counted nor listed
    _ins("Pelican Guide", "2026-05-01T10:02:00+00:00", source="safari_history", bucket="Work")
    only_chrome = _search(["pelican"], buckets=["Work"], sources=["chrome_history"])
    assert len(only_chrome) == 1 and only_chrome[0]["also_seen_in"] == [] and only_chrome[0]["occurrences"] == 1
    # the full Work grant sees both sources of the page
    full = _search(["pelican"], buckets=["Work"])
    assert len(full) == 1 and full[0]["occurrences"] == 2
    assert full[0]["also_seen_in"] == sorted({"chrome_history", "safari_history"} - {full[0]["source"]})


def test_non_browser_identical_text_still_folds_across_days(db) -> None:
    for day in (1, 2, 3):
        _ins("pelican dashboard", f"2026-05-0{day}T10:00:00+00:00", source="claude_code")
    hits = _search(["pelican"], buckets=["Work"])
    assert len(hits) == 1 and hits[0]["occurrences"] == 3 and hits[0]["also_seen_in"] == []


# ── superseded progressive-typing snapshots ─────────────────────────────────────────────────────
def _snapshot_chain(app="Notes"):
    texts = ["pelican ro", "pelican rollout", "pelican rollout pl", "pelican rollout plan nihao",
             "pelican rollout plan 你好"]
    return [_ins(text, f"2026-05-01T10:00:{i * 2:02d}+00:00", source="ax_text", bucket="Work",
                 app=app, event_type="ax_text_capture") for i, text in enumerate(texts)]


def test_snapshot_chain_leaves_only_last_state_searchable(db) -> None:
    ids = _snapshot_chain()
    hits = _search(["pelican"], buckets=["Work"])
    assert [h["id"] for h in hits] == [ids[-1]]
    rows, _ = timeline_events(since=None, until=None, sources=None, buckets=["Work"],
                              event_type=("ax_text_capture",))
    assert [r["id"] for r in rows] == [ids[-1]]
    # evidence for the first, superseded state still resolves
    first_evidence = get_conn().execute("SELECT MIN(id) FROM evidence WHERE event_id = ?", (ids[0],)).fetchone()[0]
    assert scoped_evidence(first_evidence, sources=None, buckets=["Work"],
                           event_type=("ax_text_capture",)) is not None


def test_snapshot_supersede_needs_window_and_same_app(db) -> None:
    a = _ins("pelican rollout", "2026-05-01T10:00:00+00:00", source="ax_text", app="Notes",
             event_type="ax_text_capture")
    far = _ins("pelican rollout plan", "2026-05-01T10:05:00+00:00", source="ax_text", app="Notes",
               event_type="ax_text_capture")
    other_app = _ins("pelican rollout plan", "2026-05-01T10:05:01+00:00", source="ax_text", app="Mail",
                     event_type="ax_text_capture")
    conn = get_conn()
    meta = {rid: conn.execute("SELECT metadata_json FROM raw_events WHERE id=?", (rid,)).fetchone()[0]
            for rid in (a, far, other_app)}
    assert all("superseded_by" not in m for m in meta.values())


def test_keyboard_chunks_are_not_snapshots(db) -> None:
    _ins("pelican", "2026-05-01T10:00:00+00:00", source="keyboard_chunk", app="Notes",
         event_type="keyboard_chunk")
    _ins("pelican rollout", "2026-05-01T10:00:05+00:00", source="keyboard_chunk", app="Notes",
         event_type="keyboard_chunk")
    assert len(_search(["pelican"], buckets=["Work"])) == 2


def test_migration_v33_marks_chain_and_is_idempotent(db) -> None:
    ids = _snapshot_chain()
    conn = get_conn()
    # Simulate rows stored before the capture-time rule existed: clear the marks, keep other keys.
    conn.execute("UPDATE raw_events SET metadata_json = json_remove(metadata_json, '$.superseded_by') "
                 "WHERE source = 'ax_text'")
    conn.commit()
    assert len(_search(["pelican"], buckets=["Work"])) == 5  # all states visible again
    migrate_v33(conn)
    conn.commit()

    def marks():
        return dict(conn.execute("SELECT id, json_extract(metadata_json, '$.superseded_by') "
                                 "FROM raw_events WHERE source = 'ax_text'").fetchall())

    first = marks()
    assert first[ids[0]] == ids[1] and first[ids[3]] == ids[4] and first[ids[4]] is None
    meta = json.loads(conn.execute("SELECT metadata_json FROM raw_events WHERE id=?", (ids[0],)).fetchone()[0])
    assert meta["session_id"] == "s"  # other metadata kept
    migrate_v33(conn)
    conn.commit()
    assert marks() == first
    assert [h["id"] for h in _search(["pelican"], buckets=["Work"])] == [ids[-1]]
