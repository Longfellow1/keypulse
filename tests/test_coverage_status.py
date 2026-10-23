"""M2-2 (#62): search_context / get_timeline carry coverage.by_source. Synthetic data only."""
from __future__ import annotations

import json

import pytest

from mcp_helpers import search
from penpal.mcp import auth, tools
from penpal.store.db import close, init_db
from penpal.store.models import RawEvent
from penpal.store.repository import insert_raw_event

OUT_SRC = "KPCANARYsecretsource"  # a source name that exists only outside the grant


@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    close()
    init_db(tmp_path / "cov.db")
    n = [0]

    def put(source, bucket, ts, text="pelican note"):
        n[0] += 1
        insert_raw_event(RawEvent(source=source, event_type="semantic_event", ts_start=ts, content_text=text,
                                  content_hash=f"c{n[0]}", bucket=bucket))

    put("claude_code", "Work", "2026-03-01T09:00:00+00:00")
    put("claude_code", "Work", "2026-03-10T09:00:00+00:00")
    put("git_log", "Work", "2026-02-01T09:00:00+00:00")
    put(OUT_SRC, "Personal", "2026-03-05T09:00:00+00:00")
    put(OUT_SRC, "Financial", "2026-03-06T09:00:00+00:00")
    yield
    close()


def _by_source(resp):
    return {s["source"]: s for s in resp["coverage"]["by_source"]}


def test_search_reports_span_and_range_presence(db) -> None:
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, ["pelican"], {"start": "2026-03-01", "end": "2026-03-31"})
    cov = _by_source(resp)
    assert set(cov) == {"claude_code", "git_log"}
    assert cov["claude_code"] == {"source": "claude_code", "earliest": "2026-03-01T09:00:00+00:00",
                                  "latest": "2026-03-10T09:00:00+00:00", "has_events_in_range": True}
    # git_log has data, but none in March: "no data in that period", not "never recorded"
    assert cov["git_log"]["has_events_in_range"] is False
    assert cov["git_log"]["earliest"] == cov["git_log"]["latest"] == "2026-02-01T09:00:00+00:00"


def test_no_range_means_overall(db) -> None:
    token = auth.create_grant("host", buckets=["Work"])
    assert all(s["has_events_in_range"] for s in search(token, ["pelican"])["coverage"]["by_source"])


def test_no_results_still_carries_coverage(db) -> None:
    token = auth.create_grant("host", buckets=["Work"])
    resp = search(token, ["nonexistentword"])
    assert resp["status"] == "no_results"
    assert {s["source"] for s in resp["coverage"]["by_source"]} == {"claude_code", "git_log"}


def test_empty_in_scope_source_is_reported(db) -> None:
    token = auth.create_grant("host", buckets=["Work"], sources=["claude_code", "zsh_history"])
    cov = _by_source(search(token, ["pelican"]))
    assert set(cov) == {"claude_code", "zsh_history"}
    assert cov["zsh_history"] == {"source": "zsh_history", "earliest": None, "latest": None, "has_events_in_range": False}


def test_out_of_scope_source_never_named_and_nothing_counted(db) -> None:
    token = auth.create_grant("host", buckets=["Work"])
    for resp in (search(token, ["pelican"]), search(token, [OUT_SRC], sources=[OUT_SRC]),
                 tools.get_timeline(token, "2026-01-01", "2026-12-31", client="host"),
                 tools.get_timeline(token, "2026-01-01", "2026-12-31", sources=[OUT_SRC], client="host")):
        text = json.dumps(resp)
        assert OUT_SRC not in text
        assert "excluded" not in text
        assert set(resp["coverage"]) == {
            "partial", "note", "time_range", "buckets", "sources", "by_source", "unavailable_sources",
        }


def test_source_grant_hides_same_bucket_other_source(db) -> None:
    token = auth.create_grant("host", buckets=["Work"], sources=["claude_code"])
    assert set(_by_source(search(token, ["pelican"]))) == {"claude_code"}


def test_timeline_coverage_respects_range(db) -> None:
    token = auth.create_grant("host", buckets=["Work"])
    resp = tools.get_timeline(token, "2026-03-01", "2026-03-02", client="host")
    cov = _by_source(resp)
    assert cov["claude_code"]["has_events_in_range"] is True
    assert cov["git_log"]["has_events_in_range"] is False
    empty = tools.get_timeline(token, "2025-01-01", "2025-01-02", client="host")
    assert empty["status"] == "no_results"
    assert not any(s["has_events_in_range"] for s in empty["coverage"]["by_source"])


def test_tool_descriptions_tell_hosts_to_use_coverage() -> None:
    from penpal.mcp.server import SEARCH_DESCRIPTION, TIMELINE_DESCRIPTION
    for text in (SEARCH_DESCRIPTION, TIMELINE_DESCRIPTION):
        assert "coverage.by_source" in text and "has_events_in_range" in text
