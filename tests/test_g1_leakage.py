"""G1 越权泄露 = 0 (Eval PRD §3.1.3) -- BLOCKING. Synthetic data only.

Three parts:
  1. canary scan   : unique strings planted in every out-of-scope bucket / source, hit with an
                     adversarial query battery; the canary must never appear in any response.
  2. differential  : same grant, same queries, two DBs that differ only by out-of-scope data;
                     responses (ids / timestamps stripped) must be byte-identical.  A deliberately
                     injected count leak must be caught (the harness is itself tested).
  3. coverage guard: MCP tools and response fields must be enumerated in G1_COVERED_* below; adding
                     a tool or field without extending this suite fails the build.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from mcp_helpers import search, server_for
from keypulse.mcp import auth, tools
from keypulse.mcp.server import build_server
from keypulse.store.db import close, get_conn, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event
from keypulse.store.sync_repo import add_evidence


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Config.load() must never see a real ~/.keypulse/config.toml or a cwd config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)


# ── coverage list: UPDATE THIS (and the batteries) when the MCP surface changes ──────────────
G1_COVERED_TOOLS = {"search_context": {"query", "time_range", "sources", "limit"}}
G1_COVERED_RESPONSE_FIELDS = {
    "status", "reason_code", "message", "next_action",
    "results", "results[].id", "results[].snippet", "results[].occurred_at", "results[].source",
    "results[].bucket", "results[].evidence_id", "results[].match_reason",
    "coverage", "coverage.partial", "coverage.note", "coverage.time_range",
    "coverage.time_range.start", "coverage.time_range.end", "coverage.buckets", "coverage.sources",
}

# ── synthetic corpus ─────────────────────────────────────────────────────────────────────────
SHARED_WORD = "pelican"  # present in-scope AND out-of-scope, so queries on it exercise the filter

IN_SCOPE = [  # (source, bucket, text, ts)
    ("claude_code", "Work", f"{SHARED_WORD} migration plan for the billing service", "2026-03-01T09:00:00+00:00"),
    ("claude_code", "Work", f"follow-up on {SHARED_WORD} rollout checklist", "2026-03-02T09:00:00+00:00"),
    ("git_log", "Work", f"fix {SHARED_WORD} retry loop", "2026-03-03T09:00:00+00:00"),
]

# Each out-of-scope row carries its own canary. (source, bucket, event_type, canary)
OUT_OF_SCOPE = [
    ("chrome_history", "Personal", "semantic_event", "KPCANARY1personalbrowse"),
    ("wechat", "Private", "semantic_event", "KPCANARY2privatechat"),
    ("knowledgec", "Financial", "semantic_event", "KPCANARY3bankstatement"),
    ("mystery_source", None, "semantic_event", "KPCANARY4nobucketrow"),
    ("codex_cli", "Work", "semantic_event", "KPCANARY5workothersource"),   # in bucket, out of source scope
    ("claude_code", "Work", "keyboard_chunk", "KPCANARY6rawstreamwork"),   # raw stream: never searchable
    ("claude_code", "Personal", "semantic_event", "KPCANARY7samesrcotherbucket"),
]
CANARIES = [c for *_, c in OUT_OF_SCOPE]
SOURCE_DEPENDENT = "KPCANARY5workothersource"  # in-scope for a Work grant with no source list; out-of-scope otherwise
OUT_SOURCES = sorted({s for s, *_ in OUT_OF_SCOPE})
OUT_BUCKETS = ["Personal", "Private", "Financial", "Temporary"]


def _insert(source, bucket, text, ts, event_type="semantic_event", n=[0]) -> int:
    n[0] += 1
    row_id = insert_raw_event(
        RawEvent(
            source=source, event_type=event_type, ts_start=ts, content_text=text,
            content_hash=f"h{n[0]}-{hash(text) & 0xFFFF}-{source}-{ts}", bucket=bucket or "",
        )
    )
    if bucket is None:  # force a genuinely NULL bucket (insert_raw_event would resolve one)
        get_conn().execute("UPDATE raw_events SET bucket = NULL WHERE id = ?", (row_id,))
        get_conn().commit()
    add_evidence(row_id, f"/synthetic/{source}.jsonl", 1, None)
    return row_id


def _is_in_scope(canary: str, scope: str) -> bool:
    return canary == SOURCE_DEPENDENT and "sources" not in GRANT_SCOPES[scope]


def _canaries_out_of_scope(scope: str) -> list[str]:
    return [c for c in CANARIES if not _is_in_scope(c, scope)]


def _seed(db: Path, *, with_out_of_scope: bool, scope: str) -> None:
    close()
    init_db(db)
    for source, bucket, text, ts in IN_SCOPE:
        _insert(source, bucket, text, ts)
    for source, bucket, event_type, canary in OUT_OF_SCOPE:
        if _is_in_scope(canary, scope):  # identical in both DBs: part of the granted data
            _insert(source, bucket, f"{SHARED_WORD} secret note {canary} end", "2026-03-02T10:00:00+00:00", event_type)
    if with_out_of_scope:
        for source, bucket, event_type, canary in OUT_OF_SCOPE:
            if _is_in_scope(canary, scope):
                continue
            _insert(source, bucket, f"{SHARED_WORD} secret note {canary} end", "2026-03-02T10:00:00+00:00", event_type)
        for i in range(30):  # volume, so counts/limits would differ if they leaked
            _insert("chrome_history", "Personal", f"{SHARED_WORD} bulk {i}", f"2026-03-04T09:{i:02d}:00+00:00")


GRANT_SCOPES = {
    "work_all_sources": dict(buckets=["Work"]),
    "work_claude_only": dict(buckets=["Work"], sources=["claude_code"]),
}


# ── query batteries ──────────────────────────────────────────────────────────────────────────
def _adversarial_queries() -> list[dict]:
    qs: list[dict] = []
    for c in CANARIES:
        mid = len(c) // 2
        for q in (c, c.lower(), c[:8], c[-8:], c[mid - 3 : mid + 3], f"{c}*", f"^{c}", f"-{c}",
                  f"content_text:{c}", f"{{content_text window_title}}:{c}", f'"{c}"', f'"{c}',
                  f"{c} OR {SHARED_WORD}", f"{SHARED_WORD} OR {c}", f"{SHARED_WORD} NOT {c}",
                  f"NEAR({SHARED_WORD} {c})", f"NEAR({SHARED_WORD} {c}, 5)", f"{c})", f"({c}",
                  f"%{c}%", f"_{c[1:]}", c[:3], c[:2]):
            qs.append({"query": q})
        qs.append({"query": c, "time_range": {"start": "1970-01-01", "end": "2999-12-31"}})
        qs.append({"query": c, "time_range": {"start": c}})        # garbage bound containing the canary
        qs.append({"query": c, "sources": OUT_SOURCES})
        qs.append({"query": c, "sources": [c]})
        qs.append({"query": c, "limit": 100})
    for q in ("*", "**", "OR", "AND", "NOT", "NEAR", "NEAR(", '"', '""', "'", "(", ")", ":", "content_text:", "^",
              "KPCANARY", "KPCANARY*", "kpcanary", "canary", "%", "_", "\\", "a" * 1000, "a" * 5000,
              "KPCANARY " * 400, "\x00", "🙂", "密码"):
        qs.append({"query": q})
        qs.append({"query": q, "sources": OUT_SOURCES})
    for src in OUT_SOURCES + ["no_such_source", ""]:
        qs.append({"query": SHARED_WORD, "sources": [src]})
    qs.append({"query": SHARED_WORD, "sources": []})
    qs.append({"query": SHARED_WORD, "sources": OUT_SOURCES + ["claude_code"]})
    return qs


def _differential_queries() -> list[dict]:
    qs = _adversarial_queries()
    for limit in (1, 2, 3, 10, 100):
        qs.append({"query": SHARED_WORD, "limit": limit})
    qs += [
        {"query": "billing"}, {"query": "retry loop"}, {"query": "nothing-matches-this"},
        {"query": SHARED_WORD, "time_range": {"start": "2026-03-02", "end": "2026-03-03"}},
        {"query": SHARED_WORD, "time_range": {"end": "2026-02-01"}},
        {"query": SHARED_WORD, "time_range": {"start": "bad"}},
        {"query": SHARED_WORD, "sources": ["claude_code"]},
        {"query": "  "}, {"query": ""},
    ]
    return qs


def _run(token, q: dict) -> dict:
    return search(token, q["query"], q.get("time_range"), q.get("sources"), q.get("limit", 10))


# ── 1. canary scan ───────────────────────────────────────────────────────────────────────────
@pytest.fixture
def canary_db(tmp_path, request):
    scope = request.node.callspec.params.get("scope", "work_claude_only") if hasattr(request.node, "callspec") else "work_claude_only"
    _seed(tmp_path / "canary.db", with_out_of_scope=True, scope=scope)
    yield scope
    close()


def _assert_no_canary(text: str, context: str, scope: str = "work_claude_only") -> None:
    for c in _canaries_out_of_scope(scope):
        assert c not in text, f"G1 FAIL: canary {c} leaked ({context})"
        assert c.lower() not in text.lower(), f"G1 FAIL: canary {c} leaked case-folded ({context})"


@pytest.mark.parametrize("scope", GRANT_SCOPES)
def test_g1_canary_scan_tools_layer(canary_db, scope) -> None:
    token = auth.create_grant("host", **GRANT_SCOPES[scope])
    for q in _adversarial_queries():
        resp = _run(token, q)
        _assert_no_canary(json.dumps(resp, ensure_ascii=False), repr(q)[:80], scope)
        assert resp["status"] in {"ok", "no_results", "invalid_argument"}
        if resp["status"] == "ok":  # only planted in-scope rows may ever come back
            assert all(SHARED_WORD in r["snippet"] for r in resp["results"])
            assert {r["bucket"] for r in resp["results"]} == {"Work"}


@pytest.mark.parametrize("scope", GRANT_SCOPES)
def test_g1_canary_scan_through_mcp_transport(canary_db, scope) -> None:
    """Same scan through the real MCP server: covers error text and protocol-level validation errors."""
    token = auth.create_grant("host", **GRANT_SCOPES[scope])
    server = server_for(token)
    c = CANARIES[0]
    calls = [{"query": q["query"], **{k: v for k, v in q.items() if k != "query"}} for q in _adversarial_queries()[::7]]
    calls += [
        {"query": c, "limit": c},                # wrong type carrying the canary
        {"query": c, "sources": c},
        {"query": c, "time_range": c},
        {"query": c, "time_range": {"start": c, "end": c}},
        {"query": c, "unexpected": c},
        {"query": [c]},
    ]

    async def run():
        out = []
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            for args in calls:
                res = await client.call_tool("search_context", args)
                out.append(res.model_dump_json())
        return out

    for args, text in zip(calls, asyncio.run(run())):
        _assert_no_canary(text, repr(args)[:80], scope)


def test_g1_canary_scan_denied_and_failure_paths(canary_db, monkeypatch) -> None:
    token = auth.create_grant("host", buckets=["Work"])
    for bad in (None, "", "kp_wrong", CANARIES[0]):
        _assert_no_canary(json.dumps(search(bad, CANARIES[0])), "bad token")
    auth.revoke_grants("host")
    _assert_no_canary(json.dumps(search(token, CANARIES[0])), "revoked")

    token2 = auth.create_grant("host2", buckets=["Work"])

    def boom(*a, **k):
        raise sqlite3.OperationalError(f"fts5: syntax error near {CANARIES[0]} in table raw_events_fts")

    monkeypatch.setattr(tools, "search_events", boom)
    resp = search(token2, CANARIES[0])
    assert resp["status"] == "error"
    _assert_no_canary(json.dumps(resp), "sqlite error")
    assert "raw_events" not in json.dumps(resp)


def test_g1_sanity_canaries_are_findable_with_enough_scope(canary_db) -> None:
    """Guards against a vacuous scan: the planted data IS reachable when the scope allows it."""
    token = auth.create_grant("wide", buckets=["Work", "Personal", "Private", "Financial"])
    for canary in ("KPCANARY1personalbrowse", "KPCANARY2privatechat", "KPCANARY3bankstatement",
                   "KPCANARY5workothersource", "KPCANARY7samesrcotherbucket"):
        assert search(token, canary)["status"] == "ok", canary
    # never reachable by any grant: NULL bucket (unmapped source) and raw capture streams
    for canary in ("KPCANARY4nobucketrow", "KPCANARY6rawstreamwork"):
        assert search(token, canary)["status"] == "no_results", canary


def test_at05_work_only_host_sees_only_work_in_mixed_hits(canary_db) -> None:
    token = auth.create_grant("work-host", buckets=["Work"])
    resp = search(token, SHARED_WORD, limit=100)
    assert resp["status"] == "ok"
    assert {r["bucket"] for r in resp["results"]} == {"Work"}
    assert {r["source"] for r in resp["results"]} == {"claude_code", "git_log", "codex_cli"}
    assert len(resp["results"]) == 4  # 3 in-scope + the Work/codex_cli row; not the 30 Personal bulk rows
    assert not any("secret note KPCANARY7" in r["snippet"] for r in resp["results"])


# ── 2. differential ──────────────────────────────────────────────────────────────────────────
_STRIP = {"id", "evidence_id", "occurred_at"}


def _normalize(obj):
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items() if k not in _STRIP}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return obj


def _responses_for(db: Path, with_out_of_scope: bool, scope_name: str) -> list[bytes]:
    _seed(db, with_out_of_scope=with_out_of_scope, scope=scope_name)
    token = auth.create_grant("host", **GRANT_SCOPES[scope_name])
    out = [json.dumps(_normalize(_run(token, q)), sort_keys=True, ensure_ascii=False).encode() for q in _differential_queries()]
    # denied path must also be identical
    out.append(json.dumps(search("kp_nope", SHARED_WORD), sort_keys=True).encode())
    close()
    return out


def _differential_mismatches(tmp_path: Path, scope: str) -> list[int]:
    clean = _responses_for(tmp_path / "clean.db", False, scope)
    extra = _responses_for(tmp_path / "extra.db", True, scope)
    assert len(clean) == len(extra)
    return [i for i, (a, b) in enumerate(zip(clean, extra)) if a != b]


@pytest.mark.parametrize("scope", GRANT_SCOPES)
def test_g1_differential_identical_with_and_without_out_of_scope_data(tmp_path, scope) -> None:
    mismatches = _differential_mismatches(tmp_path, scope)
    assert mismatches == [], f"G1 FAIL: {len(mismatches)} responses differ, e.g. query #{mismatches[:3]}"


def test_g1_differential_catches_injected_count_leak(tmp_path, monkeypatch) -> None:
    """The harness must fail when coverage leaks a total count (proves the differential has teeth)."""
    real = tools._coverage

    def leaky(grant, start, end):
        cov = real(grant, start, end)
        cov["total_events"] = get_conn().execute("SELECT count(*) FROM raw_events").fetchone()[0]
        return cov

    monkeypatch.setattr(tools, "_coverage", leaky)
    assert _differential_mismatches(tmp_path, "work_all_sources") != []


def test_g1_differential_catches_injected_existence_hint(tmp_path, monkeypatch) -> None:
    real = tools.search_context

    def hinting(token, query, *a, **k):
        resp = real(token, query, *a, **k)
        if resp["status"] == "no_results":  # "more results exist elsewhere" style hint
            n = get_conn().execute("SELECT count(*) FROM raw_events WHERE content_text LIKE ?", (f"%{query}%",)).fetchone()[0]
            resp["message"] = "no results" if n == 0 else "no results in your scope"
        return resp

    monkeypatch.setattr(tools, "search_context", hinting)
    assert _differential_mismatches(tmp_path, "work_all_sources") != []


# ── 3. coverage guard ────────────────────────────────────────────────────────────────────────
def _field_paths(obj, prefix="") -> set[str]:
    paths: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            paths.add(p)
            paths |= _field_paths(v, p)
    elif isinstance(obj, list):
        for item in obj:
            paths |= _field_paths(item, prefix + "[]")
    return paths


def _observed_response_fields(monkeypatch=None) -> set[str]:
    token = auth.create_grant("guard", buckets=["Work"], sources=["claude_code"])
    responses = [
        search(token, SHARED_WORD),                                   # ok
        search(token, "zzz-no-such-thing"),                           # no_results
        search(token, SHARED_WORD, time_range={"start": "bad"}),      # invalid_argument
        search("kp_nope", SHARED_WORD),                               # permission_denied
    ]
    fields: set[str] = set()
    for r in responses:
        fields |= _field_paths(r)
    return fields


def test_g1_coverage_guard_tools_and_arguments(canary_db) -> None:
    server = build_server(token_provider=lambda: None)

    async def listing():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return (await client.list_tools()).tools

    registered = {t.name: set(t.inputSchema.get("properties", {})) for t in asyncio.run(listing())}
    assert registered == G1_COVERED_TOOLS, (
        "MCP tool surface changed: extend the G1 canary/differential batteries in tests/test_g1_leakage.py "
        f"and update G1_COVERED_TOOLS. registered={registered}"
    )


def test_g1_coverage_guard_response_fields(canary_db) -> None:
    observed = _observed_response_fields()
    uncovered = observed - G1_COVERED_RESPONSE_FIELDS
    assert not uncovered, f"new MCP response fields not covered by G1: {sorted(uncovered)}"


def test_g1_coverage_guard_fails_on_new_field(canary_db, monkeypatch) -> None:
    real = tools._coverage
    monkeypatch.setattr(tools, "_coverage", lambda g, s, e: {**real(g, s, e), "hit_total": 3})
    observed = _observed_response_fields()
    assert "coverage.hit_total" in observed - G1_COVERED_RESPONSE_FIELDS
