"""Regression gate (Eval PRD §3.3.4): any metric worse than baseline by more than 2pp blocks.

Lowering the baseline or changing the eval set needs owner approval, so a changed
question/fixture digest also fails until the baseline is deliberately regenerated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

MAX_REGRESSION = 0.02  # 2 percentage points
_EPS = 1e-9


def make_baseline(report: dict[str, Any]) -> dict[str, Any]:
    """Summary numbers only (synthetic data, safe to commit)."""
    s = report["summary"]
    return {
        "eval_version": report["eval_version"],
        "question_set_digest": report["question_set_digest"],
        "m1_evidence_recall_at_5": s["m1_evidence_recall_at_5"],
        "m2_no_answer_false_hit_rate": s["m2_no_answer_false_hit_rate"],
        "answerable_evaluated": s["answerable_evaluated"],
        "no_answer_evaluated": s["no_answer_evaluated"],
        "by_scenario": {k: v["rate"] for k, v in report["by_scenario"].items()},
        "by_source": {k: v["rate"] for k, v in report["by_source"].items()},
    }


def check(report: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Return a list of blocking problems (empty = pass)."""
    problems: list[str] = []
    s = report["summary"]
    m1, m2 = s["m1_evidence_recall_at_5"], s["m2_no_answer_false_hit_rate"]
    b1, b2 = baseline["m1_evidence_recall_at_5"], baseline["m2_no_answer_false_hit_rate"]
    if (b1 - m1) > MAX_REGRESSION + _EPS:
        problems.append(f"M1 evidence recall@5 dropped {b1:.1%} -> {m1:.1%} (> 2pp)")
    if (m2 - b2) > MAX_REGRESSION + _EPS:
        problems.append(f"M2 no-answer false-hit rate rose {b2:.1%} -> {m2:.1%} (> 2pp)")
    if report["question_set_digest"] != baseline["question_set_digest"]:
        problems.append(
            "eval set / fixtures changed since the baseline was recorded "
            f"({baseline['question_set_digest']} -> {report['question_set_digest']}); "
            "changing the set or re-baselining needs owner approval (regenerate with --write-baseline)"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m evals.compare REPORT.json BASELINE.json")
        return 2
    report = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    problems = check(report, baseline)
    for p in problems:
        print(f"GATE FAIL: {p}")
    if not problems:
        print("gate: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
