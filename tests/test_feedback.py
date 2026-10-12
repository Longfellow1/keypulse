"""M2-3 (#63): rate_result + `keypulse feedback list/export`. Synthetic data only."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from mcp.shared.memory import create_connected_server_and_client_session

from evals.realdata.questions import load_questions
from mcp_helpers import search, server_for
from keypulse.config import Config
from keypulse.feedback_cli import draft_question, feedback_group
from keypulse.mcp import auth, tools
from keypulse.store import buckets, feedback
from keypulse.store.db import close, get_conn, init_db
from keypulse.store.migrations import run_migrations
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event
from keypulse.store.sync_repo import add_evidence

CANARY = "KPCANARYfeedbackprivate"
OK = {"status": "ok", "recorded": True}
BAD_ARG = {"status": "invalid_argument", "message": tools.RATE_ARG_MESSAGE}


def _put(source, bucket, text, ts, n=[0]) -> int:
    n[0] += 1
    rid = insert_raw_event(RawEvent(source=source, event_type="semantic_event", ts_start=ts, content_text=text,
                                    content_hash=f"fb{n[0]}", bucket=bucket))
    add_evidence(rid, f"/synthetic/{source}.jsonl", 1, None)
    return rid


@pytest.fixture
def db(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.chdir(tmp_path)
    buckets.reset_cache()
    init_db(Config.load().db_path_expanded)
    yield tmp_path
    close()
    buckets.reset_cache()


@pytest.fixture
def seeded(db):
    work = _put("claude_code", "Work", "pelican billing migration plan", "2026-03-01T09:00:00+00:00")
    work2 = _put("claude_code", "Work", "pelican unrelated lunch note", "2026-03-02T09:00:00+00:00")
    private = _put("wechat", "Private", f"pelican {CANARY}", "2026-03-03T09:00:00+00:00")
    token = auth.create_grant("host", buckets=["Work"])
    return {"token": token, "work": work, "work2": work2, "private": private}


def rate(token, result_id, verdict, note=None, query=None, client="host"):
    return tools.rate_result(token, result_id, verdict, note, query, client=client)


def _first_hit(token, kw="billing"):
    resp = search(token, [kw])
    assert resp["status"] == "ok"
    return resp["results"][0]


# ── happy path ───────────────────────────────────────────────────────────────────────────────
def test_rate_seen_event_and_evidence_ids(seeded) -> None:
    hit = _first_hit(seeded["token"])
    assert rate(seeded["token"], hit["id"], "correct") == OK
    assert rate(seeded["token"], hit["evidence_id"], "wrong", note="actually fine") == OK
    rows = feedback.feedback_rows()
    assert len(rows) == 1  # same event: latest verdict wins
    assert rows[0]["verdict"] == "wrong" and rows[0]["query"] == "billing" and rows[0]["grant_buckets"] == '["Work"]'


def test_timeline_and_get_evidence_returns_are_ratable(seeded) -> None:
    tl = tools.get_timeline(seeded["token"], "2026-03-02", "2026-03-02", client="host")
    assert rate(seeded["token"], tl["results"][0]["id"], "correct")["status"] == "ok"
    hit = _first_hit(seeded["token"])
    ev = tools.get_evidence(seeded["token"], hit["evidence_id"], client="host")
    assert rate(seeded["token"], ev["evidence"]["event_id"], "correct")["status"] == "ok"


def test_missing_with_query_only_and_validation(seeded) -> None:
    t = seeded["token"]
    assert rate(t, None, "missing", query="the visa receipt") == OK
    assert rate(t, None, "missing", query="the visa receipt") == OK  # idempotent
    assert len(feedback.feedback_rows()) == 1
    for bad in (rate(t, None, "missing"), rate(t, None, "correct"), rate(t, "evt_1", "bogus"), rate(t, 5, "correct"),
                rate(t, "evt_1", "correct", note=5), rate(t, None, "missing", query=["x"]), rate(t, "evt_1", ["correct"])):
        assert bad == BAD_ARG  # fixed text: no caller value echoed


# ── unseen ids: one generic not_found, no oracle ─────────────────────────────────────────────
def test_unseen_in_scope_id_equals_nonexistent_id(seeded) -> None:
    t = seeded["token"]
    _first_hit(t)  # sees only the 'billing' row
    unseen = rate(t, f"evt_{seeded['work2']}", "correct")      # exists, in scope, never returned
    ghost = rate(t, "evt_999999", "correct")
    junk = [rate(t, x, "correct") for x in ("evd_999999", "evt_", "EVT_1", "evt_-1", "evt_1 OR 1=1", "\x00", "evt_٣", " evt_1")]
    assert unseen == ghost == tools.RATE_NOT_FOUND
    assert all(j == ghost for j in junk)
    assert feedback.feedback_rows() == []


def test_other_clients_ids_are_not_ratable(seeded) -> None:
    hit = _first_hit(seeded["token"])
    other = auth.create_grant("other", buckets=["Work"])
    assert rate(other, hit["id"], "correct", client="other") == tools.RATE_NOT_FOUND
    assert rate(other, hit["evidence_id"], "wrong", client="other") == tools.RATE_NOT_FOUND


def test_requires_valid_grant(seeded) -> None:
    hit = _first_hit(seeded["token"])
    assert rate("kp_nope", hit["id"], "correct")["status"] == "permission_denied"
    assert rate(seeded["token"], hit["id"], "correct", client="wrong-client")["status"] == "permission_denied"


def test_scope_narrowed_after_return_makes_id_unratable(seeded) -> None:
    hit = _first_hit(seeded["token"])
    get_conn().execute("UPDATE raw_events SET bucket = 'Personal' WHERE id = ?", (seeded["work"],))
    get_conn().commit()
    assert rate(seeded["token"], hit["id"], "correct") == tools.RATE_NOT_FOUND


# ── TTL ──────────────────────────────────────────────────────────────────────────────────────
def test_returned_id_expires_after_ttl(seeded) -> None:
    hit = _first_hit(seeded["token"])
    old = (datetime.now(timezone.utc) - timedelta(days=feedback.RETURNED_TTL_DAYS, minutes=1)).isoformat()
    get_conn().execute("UPDATE mcp_returned_results SET returned_at = ?", (old,))
    get_conn().commit()
    assert rate(seeded["token"], hit["id"], "correct") == tools.RATE_NOT_FOUND
    fresh = (datetime.now(timezone.utc) - timedelta(days=feedback.RETURNED_TTL_DAYS - 1)).isoformat()
    get_conn().execute("UPDATE mcp_returned_results SET returned_at = ?", (fresh,))
    get_conn().commit()
    assert rate(seeded["token"], hit["id"], "correct") == OK


def test_expired_rows_are_purged_on_next_write(seeded) -> None:
    _first_hit(seeded["token"])
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    get_conn().execute("UPDATE mcp_returned_results SET returned_at = ?", (old,))
    get_conn().commit()
    _first_hit(seeded["token"], "lunch")
    n = get_conn().execute("SELECT COUNT(*) FROM mcp_returned_results WHERE returned_at < ?", (feedback._cutoff(),)).fetchone()[0]
    assert n == 0


# ── redaction / caps / no echo ───────────────────────────────────────────────────────────────
def test_note_and_query_are_redacted_and_capped(seeded) -> None:
    hit = _first_hit(seeded["token"])
    secret = "sk-" + "a1B2c3D4" * 6
    resp = rate(seeded["token"], hit["id"], "wrong", note=f"bad, mail me at jane.doe@example.com token {secret} " + "x" * 2000)
    assert resp == OK  # nothing echoed back
    row = feedback.feedback_rows()[0]
    assert "jane.doe@example.com" not in row["note"] and secret not in row["note"]
    assert len(row["note"]) <= feedback.MAX_NOTE_CHARS
    rate(seeded["token"], None, "missing", query=f"find {secret} " + "q" * 1000)
    miss = [r for r in feedback.feedback_rows() if r["verdict"] == "missing"][0]
    assert secret not in miss["query"] and len(miss["query"]) <= feedback.MAX_QUERY_CHARS


def test_ledger_stores_redacted_keywords_only(seeded) -> None:
    secret = "sk-" + "Zz9Yy8Xx" * 6
    search(seeded["token"], ["billing", secret])
    stored = get_conn().execute("SELECT query FROM mcp_returned_results").fetchone()[0]
    assert secret not in stored and "billing" in stored


def test_per_client_cap(seeded, monkeypatch) -> None:
    monkeypatch.setattr(feedback, "MAX_FEEDBACK_PER_CLIENT", 2)
    t = seeded["token"]
    assert rate(t, None, "missing", query="one") == OK
    assert rate(t, None, "missing", query="two") == OK
    assert rate(t, None, "missing", query="three")["status"] == "rate_limited"
    assert rate(t, None, "missing", query="two") == OK  # updating an existing one is fine


# ── migration ────────────────────────────────────────────────────────────────────────────────
def test_migration_v29_is_idempotent(db) -> None:
    conn = get_conn()
    conn.execute("DELETE FROM _schema_version WHERE version >= 29")  # re-run v29 through v31 on top
    conn.commit()
    run_migrations(conn)
    run_migrations(conn)
    assert conn.execute("SELECT COUNT(*) FROM _schema_version WHERE version = 29").fetchone()[0] == 1
    assert len(conn.execute("SELECT name FROM sqlite_master WHERE name IN ('mcp_returned_results','mcp_feedback')").fetchall()) == 2


# ── MCP surface ──────────────────────────────────────────────────────────────────────────────
def test_rate_result_through_mcp_transport(seeded) -> None:
    hit = _first_hit(seeded["token"])
    server = server_for(seeded["token"])

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            ok = await client.call_tool("rate_result", {"result_id": hit["id"], "verdict": "correct"})
            bad = await client.call_tool("rate_result", {"result_id": f"evt_{seeded['private']}", "verdict": "correct"})
            ghost = await client.call_tool("rate_result", {"result_id": "evt_424242", "verdict": "correct"})
            return names, ok, bad, ghost

    names, ok, bad, ghost = asyncio.run(run())
    assert "rate_result" in names
    assert ok.structuredContent == OK
    assert bad.structuredContent == ghost.structuredContent == tools.RATE_NOT_FOUND
    assert CANARY not in bad.model_dump_json()


# ── G1: feedback cannot probe other buckets ──────────────────────────────────────────────────
def _probe_all_ids(token, upto) -> list[dict]:
    out = []
    for i in range(0, upto):
        for prefix in ("evt", "evd"):
            for verdict in ("correct", "wrong", "missing"):
                out.append(rate(token, f"{prefix}_{i}", verdict, note=CANARY, query=CANARY))
    return out


def test_g1_feedback_cannot_probe_other_buckets(seeded) -> None:
    t = seeded["token"]
    _first_hit(t)
    private_client = auth.create_grant("private-host", buckets=["Private"])
    saw = tools.search_context(private_client, [CANARY], client="private-host")
    assert saw["status"] == "ok"  # the Private client legitimately saw the canary row
    seen_private = saw["results"][0]
    for r in _probe_all_ids(t, 40):
        assert r in (tools.RATE_NOT_FOUND, OK)  # OK only for the single Work id this client really saw
        assert CANARY not in json.dumps(r)
    assert rate(t, seen_private["id"], "correct") == tools.RATE_NOT_FOUND       # another client's seen id
    assert rate(t, seen_private["evidence_id"], "correct") == tools.RATE_NOT_FOUND
    assert {r["event_id"] for r in feedback.feedback_rows() if r["event_id"] is not None} == {seeded["work"]}


def test_g1_feedback_differential_with_and_without_out_of_scope_data(tmp_path, monkeypatch) -> None:
    """Same Work client, same probes, two DBs differing only by out-of-scope rows: byte-identical answers."""
    def responses(name, extra):
        h = tmp_path / name
        h.mkdir()
        monkeypatch.setenv("HOME", str(h))
        monkeypatch.chdir(h)
        close()
        buckets.reset_cache()
        init_db(h / "d.db")
        _put("claude_code", "Work", "pelican billing plan", "2026-03-01T09:00:00+00:00")
        _put("claude_code", "Work", "pelican second note", "2026-03-02T09:00:00+00:00")
        if extra:
            for i in range(10):
                _put("wechat", "Private", f"pelican {CANARY} {i}", f"2026-03-0{3 + i % 6}T09:00:00+00:00")
        token = auth.create_grant("host", buckets=["Work"])
        search(token, ["billing"])
        out = json.dumps(_probe_all_ids(token, 30), sort_keys=True)
        close()
        return out

    assert responses("clean", False) == responses("extra", True)


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def test_cli_list_and_export_roundtrip_with_realdata_loader(seeded, tmp_path) -> None:
    t = seeded["token"]
    hit = _first_hit(t)
    rate(t, hit["id"], "correct", note="spot on")
    other = search(t, ["lunch"])["results"][0]
    rate(t, other["id"], "wrong", note="mail jane.doe@example.com")
    rate(t, None, "missing", query="the visa receipt", note="never indexed?")
    runner = CliRunner()
    listing = runner.invoke(feedback_group, ["list"])
    assert listing.exit_code == 0, listing.output
    assert "correct=1" in listing.output and "wrong=1" in listing.output and "missing=1" in listing.output
    assert "jane.doe@example.com" not in listing.output

    out = tmp_path / "evals" / "questions.jsonl"
    res = runner.invoke(feedback_group, ["export", "--out", str(out)])
    assert res.exit_code == 0, res.output
    questions = load_questions(out)  # the M2-0 loader accepts the drafts as they are
    assert len(questions) == 3
    by_verdict = {json.loads(line)["feedback_verdict"]: json.loads(line) for line in out.read_text().splitlines()}
    assert by_verdict["correct"]["gold_event_ids"] == [f"evt_{seeded['work']}"]
    assert by_verdict["correct"]["grant"] == ["Work"] and by_verdict["correct"]["group"] == "feedback"
    assert by_verdict["correct"]["query"] == "billing"
    assert by_verdict["wrong"]["expect"] == "must_not_see"
    assert by_verdict["missing"]["query"] == "the visa receipt" and "gold_event_ids" not in by_verdict["missing"]
    assert all(q.gold_answer.startswith("TODO") for q in questions)
    assert "jane.doe@example.com" not in out.read_text()
    assert oct(out.stat().st_mode & 0o777) == "0o600"

    again = runner.invoke(feedback_group, ["export", "--out", str(out)])
    assert again.exit_code != 0 and "exists" in again.output


def test_cli_export_refuses_inside_git_work_tree(seeded) -> None:
    inside = Path(__file__).resolve().parent / "should_not_exist.jsonl"
    res = CliRunner().invoke(feedback_group, ["export", "--out", str(inside)])
    assert res.exit_code != 0 and "git work tree" in res.output
    assert not inside.exists()


def test_export_skips_rows_without_query_context(seeded) -> None:
    ev = tools.get_evidence(seeded["token"], _first_hit(seeded["token"])["evidence_id"], client="host")
    get_conn().execute("UPDATE mcp_returned_results SET query = NULL")
    get_conn().commit()
    rate(seeded["token"], ev["evidence"]["event_id"], "correct")
    assert feedback.feedback_rows()[0]["query"] is None
    assert draft_question(feedback.feedback_rows()[0]) is None
