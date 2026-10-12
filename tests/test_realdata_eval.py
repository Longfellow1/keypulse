"""evals/realdata harness: synthetic DBs and a fake `claude` only. No real data, no real model calls."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from evals.realdata import judge, report
from evals.realdata.__main__ import main
from evals.realdata.paths import REPO_ROOT, resolve_data_dir
from evals.realdata.prepare import contamination_counts, strip_transcripts
from evals.realdata.questions import QuestionError, load_questions, parse_question
from evals.realdata.replay import check_buckets, forbidden_hits, scan_leaks
from evals.realdata.sandbox import build_profile
from keypulse.store.db import close, init_db


def _q(**kw):
    base = {"id": "q1", "group": "A", "grant": ["Work"], "query": "what?", "gold_answer": "gold"}
    return parse_question({**base, **kw})


# ---- question parsing ----

def test_question_parsing_accepts_full_and_minimal(tmp_path):
    p = tmp_path / "q.jsonl"
    p.write_text(json.dumps({"id": "a", "group": "A", "grant": ["Work", "Personal"], "query": "q", "gold_answer": "g"}) + "\n\n"
                 + json.dumps({"id": "b", "group": "D", "grant": ["Work"], "query": "q", "gold_answer": "-",
                               "expect": "must_not_see", "gold_event_ids": [12, "evt_13"], "gold_match": "zzz",
                               "human_verdicts": {"kp": "correct"}}) + "\n")
    a, b = load_questions(p)
    assert a.expect == "answer" and a.grant == ("Personal", "Work")
    assert b.event_ids == {"evt_12", "evt_13"} and b.gold_match == ("zzz",)


@pytest.mark.parametrize("patch", [{"expect": "maybe"}, {"grant": []}, {"grant": "Work"}, {"query": ""},
                                   {"expect": "must_not_see"}, {"human_verdicts": {"kp": "great"}}])
def test_question_parsing_rejects_bad_input(patch):
    with pytest.raises(QuestionError):
        _q(**patch)


def test_question_file_rejects_bad_json_and_duplicates(tmp_path):
    p = tmp_path / "q.jsonl"
    p.write_text("{not json}\n")
    with pytest.raises(QuestionError, match="line 1"):
        load_questions(p)
    row = json.dumps({"id": "a", "group": "A", "grant": ["Work"], "query": "q", "gold_answer": "g"})
    p.write_text(row + "\n" + row + "\n")
    with pytest.raises(QuestionError, match="duplicate"):
        load_questions(p)


def test_data_dir_never_inside_repo():
    with pytest.raises(SystemExit):
        resolve_data_dir(REPO_ROOT / "evals" / "data")
    with pytest.raises(SystemExit):
        resolve_data_dir(REPO_ROOT)


# ---- sandbox profile ----

def test_profile_denies_paths_and_regexes():
    prof = build_profile([Path("/Users/x/.keypulse"), "/tmp/evals"], ["abc-123", r"/\.claude/projects/[^/]*foo"])
    assert prof.startswith("(version 1)\n(allow default)\n(deny file-read* file-write*")
    assert '(subpath "/Users/x/.keypulse")' in prof and '(subpath "/tmp/evals")' in prof
    assert '(regex #"abc-123")' in prof and r'(regex #"/\.claude/projects/[^/]*foo")' in prof
    assert prof.rstrip().endswith(")")


def test_profile_rejects_unsafe_input():
    with pytest.raises(ValueError):
        build_profile([], [])
    with pytest.raises(ValueError):
        build_profile(["/"])
    with pytest.raises(ValueError):
        build_profile(["/a"], ['x") (allow default'])
    assert 'a\\"b' in build_profile(['/a"b'])  # path quotes are escaped, not injected


# ---- synthetic DB ----

def _make_db(path: Path) -> Path:
    init_db(path)
    conn = sqlite3.connect(path)
    n = 0

    def add(source, text, ts, bucket="Work", session=None, locator=None):
        nonlocal n
        n += 1
        cur = conn.execute(
            "INSERT INTO raw_events(source,event_type,ts_start,content_text,content_hash,created_at,bucket,session_id) "
            "VALUES (?, 'semantic_event', ?, ?, ?, ?, ?, ?)", (source, ts, text, f"h{n}", ts, bucket, session))
        if locator:
            conn.execute("INSERT INTO evidence(event_id,locator,created_at) VALUES (?,?,?)", (cur.lastrowid, locator, ts))
        return cur.lastrowid

    add("claude_code", "alpha-gold discussed in old session", "2026-01-01T10:00:00", session="old-sess")
    add("claude_code", "building the eval harness, gold-in-build-session", "2026-09-01T10:00:00", session="build-sess")
    add("codex_cli", "after cutoff gold-after", "2026-10-05T10:00:00", locator="/x/rollout-late-sess.jsonl")
    add("claude_code", "gold-in-locator-only", "2026-02-01T10:00:00", locator="/x/projects/drop-me-123/s.jsonl")
    add("chrome_history", "visited gold-in-browser", "2026-10-06T10:00:00")
    add("zsh_history", "secretword in private shell", "2026-03-01T10:00:00", bucket="Personal")
    add("git_log", "commit about secretword plumbing", "2026-03-02T10:00:00", bucket="Work")
    conn.commit()
    conn.close()
    close()
    return path


def test_strip_transcripts_removes_sessions_cutoff_and_keeps_fts_consistent(tmp_path):
    db = _make_db(tmp_path / "s.db")
    deleted = strip_transcripts(db, ["build-sess", "drop-me-123"], "2026-10-01T00:00:00")
    assert deleted == {"sessions": 2, "after": 1}
    conn = sqlite3.connect(db)
    left = {r[0] for r in conn.execute("SELECT content_text FROM raw_events")}
    assert not any("build-session" in t or "gold-after" in t or "locator-only" in t for t in left)
    assert any("alpha-gold" in t for t in left) and any("gold-in-browser" in t for t in left)  # non-transcript kept
    assert conn.execute("SELECT COUNT(*) FROM raw_events_fts WHERE raw_events_fts MATCH '\"build-session\"'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0  # cascade
    conn.close()


def test_contamination_counts_are_counts_over_transcript_rows_only(tmp_path):
    db = _make_db(tmp_path / "s.db")
    qs = [_q(id="c1", gold_match=["alpha-gold"]), _q(id="c2", gold_match=["gold-in-browser"]),
          _q(id="c3", gold_answer="nothing like this")]
    assert contamination_counts(db, qs) == {"c1": 1, "c2": 0, "c3": 0}  # browser row is not a transcript


# ---- judge parsing ----

@pytest.mark.parametrize("text", ["", "   ", "not json", "[1]", '{"verdict": "correct"}', '{"verdict": "great", "reason": "x"}',
                                  '{"verdict": "correct", "reason": ""}', '{"verdict": null, "reason": "x"}',
                                  'Sure! {"verdict": "correct", "reason": "ok"}', None])
def test_malformed_judge_output_is_error_never_correct(text):
    assert judge.parse_judge_output(text)["verdict"] == "error"


def test_valid_judge_output_parses_plain_and_fenced():
    assert judge.parse_judge_output('{"verdict": "partial", "reason": "missing source"}') == {"verdict": "partial", "reason": "missing source"}
    assert judge.parse_judge_output('```json\n{"verdict": "correct", "reason": "same"}\n```')["verdict"] == "correct"


def test_judge_prompt_states_not_found_rule():
    assert "expect=not_found" in judge.JUDGE_PROMPT and "presents nothing" in judge.JUDGE_PROMPT


# ---- must_not_see (mechanical) ----

def test_forbidden_hits_by_event_id_and_text_without_echoing_gold():
    q = _q(expect="must_not_see", gold_event_ids=[7], gold_match=["TopSecret"])
    assert forbidden_hits(q, json.dumps({"results": [{"id": "evt_70"}]})) == []  # evt_70 is not evt_7
    hits = forbidden_hits(q, json.dumps({"results": [{"id": "evt_7", "snippet": "a topsecret b"}]}))
    assert hits == ["event_id#0", "match#0"] and "TopSecret" not in "".join(hits)


def test_mechanical_verdict_from_replay():
    q = _q(expect="must_not_see", gold_match=["x"])
    assert judge.mechanical_must_not_see(q, {"forbidden_hits": []})["verdict"] == "correct"
    assert judge.mechanical_must_not_see(q, {"forbidden_hits": ["match#0"]})["verdict"] == "wrong"
    assert judge.mechanical_must_not_see(q, None)["verdict"] == "error"


def test_leak_and_bucket_checks():
    assert scan_leaks("nothing here") == []
    assert "id_number" in scan_leaks("id 11010519491231002X ok")
    assert check_buckets({"results": [{"bucket": "Work"}, {"bucket": "Personal"}], "events": [{"bucket": "Private"}]}, {"Work"}) == ["Personal", "Private"]


# ---- report aggregation ----

def _res(id_, secs, tools, cost):
    return {"id": id_, "seconds": secs, "tool_calls": tools, "cost_usd": cost}


def _ver(id_, v):
    return {"id": id_, "verdict": v, "reason": "r"}


def test_report_aggregation_exclusive_and_agreement():
    qs = {i: _q(id=i, human_verdicts=h) for i, h in
          {"a": {"kp": "correct", "bash": "wrong"}, "b": {"kp": "correct"}, "c": {}, "d": {}}.items()}
    results = {"kp": [_res("a", 10, 2, 0.1), _res("b", 20, 4, 0.2), _res("c", 30, 6, 0.3), _res("d", 5, 1, 0.0)],
               "bash": [_res("a", 40, 9, 0.5), _res("b", 50, 11, 0.5), _res("c", 60, 3, 0.5)]}
    judged = {"kp": [_ver("a", "correct"), _ver("b", "wrong"), _ver("c", "correct"), {"id": "d", "verdict": "n/a", "reason": ""}],
              "bash": [_ver("a", "wrong"), _ver("b", "correct"), _ver("c", "correct")]}
    replay = {"calls": 3, "skipped": 0, "latency_ms": {"p50": 1.0, "p95": 2.0, "max": 3.0}, "leaks": [{"id": "a", "tool": "t", "patterns": ["api_key"]}],
              "bucket_violations": [], "per_question": {}}
    agg = report.aggregate(qs, results, judged, replay)
    kp = agg["arms"]["kp"]
    assert (kp["n"], kp["correct"], kp["wrong"]) == (3, 2, 1) and kp["accuracy"] == pytest.approx(2 / 3)
    assert kp["median_seconds"] == 15 and kp["max_seconds"] == 30 and kp["median_tool_calls"] == 3
    assert kp["cost_usd"] == pytest.approx(0.6)
    assert kp["judge_disagreements"] == ["b"] and kp["judge_agreement"] == 0.5
    assert agg["exclusive"] == {"compared": 3, "kp_only": ["a"], "bash_only": ["b"]}
    md = report.render(agg, "run1")
    assert "p95 2.0" in md and "leak hits: 1 (a:api_key)" in md and "KeyPulse correct, bash not: 1" in md


# ---- end to end with a fake claude on PATH ----

FAKE_CLAUDE = """#!{py}
import json, os, sys
a = sys.argv
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(a[1:]) + "\\n")
def emit(*evs):
    for e in evs: print(json.dumps(e))
if "--tools" in a:
    emit({{"type": "result", "result": os.environ.get("FAKE_JUDGE_REPLY", "garbage"), "total_cost_usd": 0.01}})
    sys.exit(0)
if "--mcp-config" in a:
    cfg = json.load(open(a[a.index("--mcp-config") + 1]))
    assert cfg["mcpServers"]["keypulse"]["env"]["KEYPULSE_TOKEN"].startswith("kp_")
    use = {{"type": "tool_use", "name": "mcp__keypulse__search_context", "input": {{"keywords": ["secretword"]}}}}
else:
    use = {{"type": "tool_use", "name": "Bash", "input": {{"command": "grep -r secretword ~"}}}}
emit({{"type": "assistant", "message": {{"content": [use]}}}},
     {{"type": "result", "result": os.environ.get("FAKE_ANSWER", "found it"), "total_cost_usd": 0.05, "is_error": False}})
"""
FAKE_SANDBOX = "#!/bin/sh\nshift 2\nexec \"$@\"\n"


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    b = tmp_path / "bin"
    b.mkdir()
    for name, body in (("claude", FAKE_CLAUDE.format(py=sys.executable)), ("sandbox-exec", FAKE_SANDBOX)):
        (b / name).write_text(body)
        (b / name).chmod(b.joinpath(name).stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "claude.log"
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_JUDGE_REPLY", '{"verdict": "correct", "reason": "matches gold"}')
    yield log
    close()


def test_end_to_end_prepare_run_replay_judge_report(tmp_path, fake_bin, capsys):
    src = _make_db(tmp_path / "src.db")
    data = tmp_path / "evals"
    data.mkdir()
    rows = [
        {"id": "A1", "group": "A", "grant": ["Work", "Personal"], "query": "q1", "gold_answer": "g1"},
        {"id": "A2", "group": "A", "grant": ["Work"], "query": "q2", "gold_answer": "nothing exists", "expect": "not_found"},
        {"id": "D1", "group": "D", "grant": ["Work"], "query": "q3", "gold_answer": "-", "expect": "must_not_see",
         "gold_match": ["private shell"]},
        {"id": "D2", "group": "D", "grant": ["Work", "Personal"], "query": "q4", "gold_answer": "-", "expect": "must_not_see",
         "gold_match": ["private shell"], "human_verdicts": {"kp": "wrong"}},
    ]
    (data / "questions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    base = ["--data-dir", str(data)]

    assert main(["prepare", *base, "--db", str(src), "--exclude-session", "build-sess", "--authored-after", "2026-10-01T00:00:00"]) == 0
    out = capsys.readouterr().out
    assert "excluded sessions=1, after cutoff=1" in out and "g1" not in out
    run_dir = next((data / "runs").iterdir())

    assert main(["run", *base, "--arm", "kp", "--keypulse-bin", "keypulse"]) == 0
    assert main(["run", *base, "--arm", "bash", "--deny-regex", "build-sess"]) == 0
    kp = [json.loads(l) for l in (run_dir / "results-kp.jsonl").read_text().splitlines()]
    bash = [json.loads(l) for l in (run_dir / "results-bash.jsonl").read_text().splitlines()]
    assert [r["id"] for r in kp] == ["A1", "A2", "D1", "D2"]
    assert [r["id"] for r in bash] == ["A1", "A2"]  # must_not_see is KeyPulse-only
    assert all(r["exit"] == 0 and r["tool_calls"] == 1 and r["cost_usd"] == 0.05 for r in kp + bash)
    assert kp[0]["tools"][0]["args"] == {"keywords": ["secretword"]} and "args" not in bash[0]["tools"][0]
    assert 'regex #"build-sess"' in (run_dir / "bash_arm.sb").read_text()
    assert "kp_" not in "".join(p.read_text() for p in run_dir.glob("*.jsonl"))  # no token in results
    assert not list(run_dir.rglob("mcp-*.json"))

    n_calls = len(fake_bin.read_text().splitlines())
    assert main(["run", *base, "--arm", "kp"]) == 0  # resumable: nothing re-run
    assert len(fake_bin.read_text().splitlines()) == n_calls

    assert main(["replay", *base]) == 0
    replay = json.loads((run_dir / "replay.json").read_text())
    assert replay["calls"] == 4 and replay["bucket_violations"] == [] and replay["latency_ms"]["p95"] is not None
    assert replay["per_question"]["D1"]["forbidden_hits"] == []      # Work grant never sees the Personal row
    assert replay["per_question"]["D2"]["forbidden_hits"] == ["match#0"]  # broader grant does

    assert main(["judge", *base]) == 0
    jk = {j["id"]: j for j in map(json.loads, (run_dir / "judged-kp.jsonl").read_text().splitlines())}
    assert jk["A1"]["verdict"] == "correct" and jk["A1"]["method"] == "llm"
    assert (jk["D1"]["verdict"], jk["D1"]["method"]) == ("correct", "mechanical")
    assert jk["D2"]["verdict"] == "wrong"

    assert main(["report", *base]) == 0
    md = (run_dir / "report.md").read_text()
    assert "KeyPulse correct, bash not" in md and "p95" in md
    assert "g1" not in md and "q1" not in md  # no question or answer text in the report


def test_unparseable_judge_reply_yields_error_verdict(tmp_path, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_JUDGE_REPLY", "I think it is correct")
    data = tmp_path / "evals"
    (data / "runs" / "r1").mkdir(parents=True)
    q = {"id": "A1", "group": "A", "grant": ["Work"], "query": "q", "gold_answer": "g"}
    (data / "questions.jsonl").write_text(json.dumps(q) + "\n")
    (data / "runs" / "r1" / "results-kp.jsonl").write_text(json.dumps(
        {"id": "A1", "exit": 0, "answer": "some answer", "tools": [], "seconds": 1, "tool_calls": 0}) + "\n")
    assert main(["judge", "--data-dir", str(data)]) == 0
    j = json.loads((data / "runs" / "r1" / "judged-kp.jsonl").read_text())
    assert j["verdict"] == "error"


# ── transient failures (API/usage errors) are retried and rerun on resume, never graded ──────────

def test_transient_rows_are_dropped_on_resume_and_real_failures_kept(tmp_path):
    from evals.realdata.runner import done_ids, is_transient

    path = tmp_path / "results-bash.jsonl"
    rows = [
        {"id": "Q1", "exit": 0, "is_error": False, "tool_calls": 3, "answer": "ok"},
        {"id": "Q2", "exit": 1, "is_error": True, "tool_calls": 0, "answer": ""},       # API error, nothing done
        {"id": "Q3", "exit": 1, "is_error": True, "tool_calls": 5, "answer": ""},       # failed after working: keep
        {"id": "Q4", "verdict": "error", "method": "llm", "reason": "empty judge output"},
        {"id": "Q5", "verdict": "wrong", "method": "llm", "reason": "x"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert [is_transient(r) for r in rows] == [False, True, False, True, False]
    assert done_ids(path) == {"Q1", "Q3", "Q5"}
    assert {json.loads(l)["id"] for l in path.read_text().splitlines()} == {"Q1", "Q3", "Q5"}


def test_parse_stream_error_result_is_not_an_answer():
    from evals.realdata.runner import parse_stream

    out = parse_stream(json.dumps({"type": "result", "is_error": True, "result": "API Error: overloaded"}))
    assert out["is_error"] and out["answer"] == "" and "overloaded" in out["error"]


def test_judge_retries_empty_output(monkeypatch):
    calls = []

    class P:
        def __init__(self, stdout):
            self.stdout, self.returncode = stdout, 0

    def fake_run(*a, **k):
        calls.append(1)
        text = "" if len(calls) < 3 else '{"verdict": "correct", "reason": "r"}'
        return P(json.dumps({"type": "result", "result": text}) if text else "")

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    monkeypatch.setattr(judge.time, "sleep", lambda s: None)
    assert '"correct"' in judge.call_judge("p", model="m", claude="claude", retries=2, wait=0)
    assert len(calls) == 3
