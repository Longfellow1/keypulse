"""Opt-in smoke for the synthetic performance / freshness benchmark (Eval PRD E5).

Skipped by default. Run with:  PENPAL_RUN_SLOW=1 python -m pytest -q -m slow tests/test_perf_freshness.py
Keeps the script importable and its end-to-end path working at a small N. It does not judge PRD
thresholds: those are for the full 100k run on the reference machine (docs/eval/perf-freshness-v0.3.0.md).
HOME and cwd are redirected to tmp_path by the benchmark itself.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("PENPAL_RUN_SLOW") != "1", reason="slow benchmark; set PENPAL_RUN_SLOW=1"),
]

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench" / "perf_freshness.py"


def _load():
    spec = importlib.util.spec_from_file_location("perf_freshness", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fixture_is_seeded_and_reproducible():
    mod = _load()
    a, _, _ = mod.generate_events(300, 7)
    b, _, _ = mod.generate_events(300, 7)
    c, _, _ = mod.generate_events(300, 8)
    assert mod.events_digest(a) == mod.events_digest(b)
    assert mod.events_digest(a) != mod.events_digest(c)


def test_small_run_end_to_end(tmp_path):
    mod = _load()
    result = mod.run_benchmark(events=600, seed=7, query_reps=1, freshness_runs=2, dynamic_runs=2,
                               work_dir=tmp_path)
    m = result["measured"]
    assert result["db"]["raw_events_rows"] == 600
    assert m["query"]["sanity"] == {"zero_hit_all_no_results": True, "marker_exact_all_hit": True}
    assert m["freshness_structured"]["not_found"] == 0
    assert m["freshness_dynamic"]["not_found"] == 0
    assert set(result["verdicts"]) == {
        "M4_structured_freshness_p95", "M4_dynamic_freshness_p95",
        "M5_query_latency_p95_warm", "M5_query_latency_p95_cold",
    }
