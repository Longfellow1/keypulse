"""M5 baseline 报告 — 跑现状 daily 文件给出每天质量分数。

不强求 pass，作为 regression baseline。运行 `pytest tests/test_m5_daily_baseline.py -s`
看每天分数；改完 daily pipeline 后再跑对比。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from keypulse.pipeline.daily_validator import quick_score, validate_daily_output

DAILY_DIR = Path("/Users/Harland/Go/Knowledge/Daily")
WEEK_DATES = [f"2026-05-{d:02d}" for d in range(4, 10)]


@pytest.mark.skipif(
    not DAILY_DIR.exists(),
    reason="本机没有 daily 渲染目录, 跳过 baseline 报告",
)
def test_print_baseline_w19() -> None:
    print()
    print(f"{'date':12s}  {'failures':>9s}  {'errors':>7s}  {'warns':>6s}  {'score':>6s}  rules")
    print("-" * 80)
    for date_str in WEEK_DATES:
        path = DAILY_DIR / f"{date_str}.md"
        if not path.exists():
            print(f"{date_str:12s}  (missing)")
            continue
        failures = validate_daily_output(rendered_markdown=path.read_text())
        errors = sum(1 for f in failures if f.severity == "error")
        warns = sum(1 for f in failures if f.severity == "warn")
        rule_count = Counter(f.rule for f in failures)
        rules_summary = " ".join(f"{r}={n}" for r, n in rule_count.most_common())
        score = quick_score(failures)
        print(
            f"{date_str:12s}  {len(failures):>9d}  {errors:>7d}  {warns:>6d}  "
            f"{score:>6d}  {rules_summary}"
        )
    print()
