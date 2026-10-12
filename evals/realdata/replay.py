"""Replay the KeyPulse arm's recorded tool calls directly: latency, leak scan, bucket check, must_not_see."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from evals.realdata.grants import make_grants
from evals.realdata.questions import Question

ID_NUMBER_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EVT_RE = re.compile(r"evt_\d+")
REPLAYABLE = ("search_context", "get_timeline", "get_evidence")


def scan_leaks(blob: str) -> list[str]:
    """Pattern names of sensitive content (secrets / ID / cards / ...) still present in tool output."""
    from keypulse.privacy.detectors import detect

    hits = {d.pattern_name for d in detect(blob)}
    if ID_NUMBER_RE.search(blob):
        hits.add("id_number")
    return sorted(hits)


def check_buckets(payload: dict, allowed: set[str]) -> list[str]:
    bad = []
    for item in (payload.get("results") or []) + (payload.get("events") or []):
        b = item.get("bucket") if isinstance(item, dict) else None
        if b and b not in allowed:
            bad.append(b)
    return bad


def forbidden_hits(q: Question, blob: str) -> list[str]:
    """Labels only (never the gold strings themselves)."""
    hits = []
    found_ids = set(EVT_RE.findall(blob))
    hits += [f"event_id#{i}" for i, e in enumerate(sorted(q.event_ids)) if e in found_ids]
    low = blob.lower()
    hits += [f"match#{i}" for i, m in enumerate(q.gold_match) if m.strip() and m.lower() in low]
    return hits


def _call(name: str, args: dict, token: str, client: str) -> dict:
    from keypulse.mcp import tools

    if name == "search_context":
        return tools.search_context(token, args.get("keywords"), args.get("time_range"), args.get("sources"),
                                    args.get("limit", 10), args.get("include_keypulse_echoes", False), client=client)
    if name == "get_timeline":
        return tools.get_timeline(token, args.get("start"), args.get("end"), args.get("sources"),
                                  args.get("limit", 50), args.get("cursor"),
                                  args.get("include_keypulse_echoes", False), client=client)
    return tools.get_evidence(token, args.get("evidence_id"), client=client)


def percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(len(sorted_vals) * p))]


def replay_kp(questions: dict[str, Question], run_dir: Path, log=print) -> dict:
    results = run_dir / "results-kp.jsonl"
    db, home = run_dir / "keypulse.db", run_dir / "home"
    rows = [json.loads(l) for l in results.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r["id"] in questions]
    grants = make_grants(home, db, {questions[r["id"]].grant for r in rows}, "rdreplay")
    real_home = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    lat, leaks, bucket_viol, per_q, n, skipped = [], [], [], {}, 0, 0
    try:
        for r in rows:
            q = questions[r["id"]]
            cid, token = grants[q.grant]
            allowed = set(q.grant)
            hits: list[str] = []
            for t in r.get("tools", []):
                name = str(t.get("name", "")).split("__")[-1]
                args = t.get("args")
                if name not in REPLAYABLE or not isinstance(args, dict):
                    skipped += 1
                    continue
                t0 = time.perf_counter()
                payload = _call(name, args, token, cid)
                lat.append((time.perf_counter() - t0) * 1000)
                n += 1
                blob = json.dumps(payload, ensure_ascii=False)
                if (found := scan_leaks(blob)):
                    leaks.append({"id": q.id, "tool": name, "patterns": found})
                bucket_viol += [{"id": q.id, "bucket": b} for b in check_buckets(payload, allowed)]
                if q.expect == "must_not_see":
                    hits += forbidden_hits(q, blob)
            per_q[q.id] = {"forbidden_hits": sorted(set(hits))}
            log(f"{q.id} replayed")
    finally:
        if real_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = real_home
    lat.sort()
    out = {"calls": n, "skipped": skipped, "latency_ms": {"p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95),
                                                         "max": lat[-1] if lat else None},
           "leaks": leaks, "bucket_violations": bucket_viol, "per_question": per_q}
    (run_dir / "replay.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
