"""Markdown summary. Written into the run dir only."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from evals.realdata.questions import Question


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _fmt(v, nd=1) -> str:
    return "-" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def _labels(rows: list[dict], key: str, legacy: str | None) -> str:
    """Distinct values of a row field, e.g. the host or model a run used. Rows written before the field
    existed get `legacy` (None means unknown)."""
    vals = {str(r[key]) if r.get(key) is not None else legacy for r in rows}
    vals.discard(None)
    return "/".join(sorted(vals)) or "-"


def aggregate(questions: dict[str, Question], results: dict[str, list[dict]], judged: dict[str, list[dict]],
              replay: dict | None) -> dict:
    arms = {}
    verdicts = {arm: {j["id"]: j for j in js if j["verdict"] != "n/a"} for arm, js in judged.items()}
    for arm, rs in results.items():
        v = verdicts.get(arm, {})
        counted = [r for r in rs if r["id"] in v]
        n = len(counted)
        c = {k: sum(1 for r in counted if v[r["id"]]["verdict"] == k) for k in ("correct", "partial", "wrong", "error")}
        secs = [r["seconds"] for r in rs if r.get("seconds") is not None]
        costs = [r["cost_usd"] for r in rs if r.get("cost_usd") is not None]
        dis = []
        for r in counted:
            human = questions[r["id"]].human_verdicts.get(arm)
            if human and human != v[r["id"]]["verdict"]:
                dis.append(r["id"])
        compared = sum(1 for r in counted if questions[r["id"]].human_verdicts.get(arm))
        llm_judged = [j for j in judged.get(arm, []) if j.get("method") == "llm"]
        violations = [r["id"] for r in rs if r.get("non_kp_tools")]  # codex runs: anything but KeyPulse MCP calls
        arms[arm] = {
            "n": n, **c, "accuracy": c["correct"] / n if n else None,
            "host": _labels(rs, "host", "claude"), "model": _labels(rs, "model", None),
            "judge_host": _labels(llm_judged, "judge_host", "claude"),
            "judge_model": _labels(llm_judged, "judge_model", None),
            "median_seconds": statistics.median(secs) if secs else None, "max_seconds": max(secs) if secs else None,
            "median_tool_calls": statistics.median([r["tool_calls"] for r in rs]) if rs else None,
            "cost_usd": sum(costs) if costs else None,
            "isolation_violations": violations, "isolation_count": len(violations),
            "judge_compared": compared, "judge_disagreements": dis,
            "judge_agreement": (1 - len(dis) / compared) if compared else None,
        }
    exclusive = None
    if "kp" in verdicts and "bash" in verdicts:
        both = set(verdicts["kp"]) & set(verdicts["bash"])
        ok = lambda arm, i: verdicts[arm][i]["verdict"] == "correct"  # noqa: E731
        exclusive = {"compared": len(both),
                     "kp_only": sorted(i for i in both if ok("kp", i) and not ok("bash", i)),
                     "bash_only": sorted(i for i in both if ok("bash", i) and not ok("kp", i))}
    return {"arms": arms, "exclusive": exclusive, "replay": replay}


def render(agg: dict, run_name: str = "") -> str:
    L = [f"# Real-data eval report {run_name}".rstrip(), "",
         "Local only. Contains aggregates and question ids, no questions or answers.", ""]
    arms = agg["arms"]
    if arms:
        names = list(arms)
        L += ["## Per arm", "", "| metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        rows = [("run host", "host", 0), ("run model", "model", 0),
                ("judge host", "judge_host", 0), ("judge model", "judge_model", 0),
                ("graded questions", "n", 0), ("correct", "correct", 0), ("partial", "partial", 0),
                ("wrong", "wrong", 0), ("judge errors", "error", 0), ("accuracy (correct)", "accuracy", 3),
                ("median seconds", "median_seconds", 1), ("max seconds", "max_seconds", 1),
                ("median tool calls", "median_tool_calls", 1), ("cost USD", "cost_usd", 3),
                ("isolation violations", "isolation_count", 0),
                ("judge vs human agreement", "judge_agreement", 3)]
        for label, key, nd in rows:
            L.append(f"| {label} | " + " | ".join(_fmt(arms[a][key], nd) for a in names) + " |")
        L.append("")
        for a in names:
            if arms[a]["judge_disagreements"]:
                L.append(f"- Judge disagreements with human pre-labels ({a}): {', '.join(arms[a]['judge_disagreements'])}")
            if arms[a]["isolation_violations"]:
                L.append(f"- Isolation violations ({a}, tools outside KeyPulse MCP ran): {', '.join(arms[a]['isolation_violations'])}")
        L.append("")
    ex = agg["exclusive"]
    if ex:
        L += ["## Exclusive correct (questions both arms ran)", "",
              f"- compared: {ex['compared']}",
              f"- KeyPulse correct, bash not: {len(ex['kp_only'])} ({', '.join(ex['kp_only']) or '-'})",
              f"- bash correct, KeyPulse not: {len(ex['bash_only'])} ({', '.join(ex['bash_only']) or '-'})", ""]
    rp = agg["replay"]
    if rp:
        lat = rp["latency_ms"]
        L += ["## Replay of KeyPulse tool calls", "",
              f"- replayed calls: {rp['calls']} (skipped: {rp['skipped']})",
              f"- tool latency ms: p50 {_fmt(lat['p50'])}, p95 {_fmt(lat['p95'])}, max {_fmt(lat['max'])}",
              f"- leak hits: {len(rp['leaks'])}" + (f" ({'; '.join(l['id'] + ':' + ','.join(l['patterns']) for l in rp['leaks'])})" if rp["leaks"] else ""),
              f"- bucket violations: {len(rp['bucket_violations'])}", ""]
    return "\n".join(L) + "\n"


def build_report(questions: dict[str, Question], run_dir: Path) -> Path:
    results = {a: _load(run_dir / f"results-{a}.jsonl") for a in ("kp", "bash")}
    judged = {a: _load(run_dir / f"judged-{a}.jsonl") for a in ("kp", "bash")}
    results = {a: r for a, r in results.items() if r}
    judged = {a: j for a, j in judged.items() if a in results}
    rp = run_dir / "replay.json"
    replay = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else None
    out = run_dir / "report.md"
    out.write_text(render(aggregate(questions, results, judged, replay), run_dir.name), encoding="utf-8")
    return out
