from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from keypulse.pipeline.daily_summary import write_daily_summary
from keypulse.pipeline.dimension_stats import FiveDimensions, compute_five_dimensions, render_key_data_section
from keypulse.pipeline.holiday_strategy import (
    build_holiday_context,
    classify_day,
    get_active_holidays,
    load_holidays_table,
    read_marked_holiday,
    select_week_template,
)
from keypulse.pipeline.weekly_orchestrator import run_weekly
from keypulse.pipeline.weekly_validator import check_failure_narrative_has_anchor, validate_weekly_output
from keypulse.store.db import close, init_db


def _week_dates(start: str) -> list[str]:
    from datetime import date as date_cls, timedelta

    first = date_cls.fromisoformat(start)
    return [(first + timedelta(days=offset)).isoformat() for offset in range(7)]


def _seed_weekly_topics(tmp_path: Path) -> None:
    topics_dir = tmp_path / ".keypulse" / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        [
            "---",
            "type: topic",
            "slug: alpha-topic",
            "display_name: Alpha Topic",
            "first_seen: 2026-05-04",
            "last_seen: 2026-05-10",
            "keywords:",
            "  - alpha",
            "---",
            "",
            "# Alpha Topic",
            "",
            "## Entries",
            "- 2026-04-27 18:00 | 1 events | 上周推进 alpha",
            "- 2026-04-29 18:00 | 1 events | 上周继续 alpha",
            "- 2026-05-04 18:00 | 2 events | 本周推进 alpha",
            "",
            "## Related Events",
            "- [[../.keypulse/events/2026-05-04/1|1]]",
            "",
        ]
    )
    (topics_dir / "alpha-topic.md").write_text(body, encoding="utf-8")


def _seed_weekly_daily_summaries() -> None:
    dates = ["2026-05-04", "2026-05-05", "2026-05-06"]
    for idx, day in enumerate(dates):
        write_daily_summary(
            day,
            clusters=[
                {
                    "slug": "alpha-topic",
                    "display_name": "Alpha Topic",
                    "narrative_one_line": f"alpha {idx} [DECISION: 决定收敛{idx}] [SHIPPED: commit +10 -2 test]",
                    "event_count": 3,
                    "time_range": ["10:00", "10:30"],
                    "merge_candidate_with": [],
                },
                {
                    "slug": "beta-topic",
                    "display_name": "Beta Topic",
                    "narrative_one_line": "与 Claude 和 Codex 协作推进",
                    "event_count": 2,
                    "time_range": ["11:00", "11:10"],
                    "merge_candidate_with": [],
                },
            ],
            misc=[],
            topic_snapshot={
                "alpha-topic": {"name": "Alpha Topic", "state": "in_progress", "last_seen_date": day, "evidence_dates": [day]},
                "gamma-topic": {"name": "Gamma Topic", "state": "started", "last_seen_date": day, "evidence_dates": [day]},
            },
            cost={"in_tokens": 12, "out_tokens": 6, "cost_usd": 0.001},
        )


def test_load_holidays_table_has_expected_rows() -> None:
    table = load_holidays_table()
    holidays = table.get("holidays") or []
    assert len(holidays) == 80
    assert all(isinstance(item, dict) and "region" in item for item in holidays)


def test_get_active_holidays_by_week_and_region() -> None:
    week_with_cny = _week_dates("2026-02-16")
    active = get_active_holidays(week_with_cny, region="CN")
    assert any(str(item.get("name") or "").startswith("春节") for item in active)

    week_w19 = _week_dates("2026-05-04")
    empty = get_active_holidays(week_w19, region="CN")
    assert empty == []


def test_classify_day_all_four_labels() -> None:
    active = [{"date": "2026-05-01", "name": "劳动节"}]
    assert classify_day("2026-05-01", 8, active, historical_avg=10.0) == "holiday_high"
    assert classify_day("2026-05-01", 2, active, historical_avg=10.0) == "holiday_low"
    assert classify_day("2026-05-02", 8, active, historical_avg=10.0) == "workday_high"
    assert classify_day("2026-05-02", 2, active, historical_avg=10.0) == "workday_low"


def test_select_week_template_all_paths() -> None:
    dates = _week_dates("2026-05-04")
    assert select_week_template({day: "holiday_high" for day in dates}) == "still_working_in_holiday"
    assert select_week_template({day: "holiday_low" for day in dates}) == "holiday_rest"
    assert select_week_template({day: "workday_low" for day in dates}) == "slow_rhythm"
    mixed = {day: ("workday_high" if idx % 2 == 0 else "workday_low") for idx, day in enumerate(dates)}
    assert select_week_template(mixed) == "standard"


def test_build_holiday_context_low_activity_fallback() -> None:
    ctx = build_holiday_context(
        week_str="2026-W19",
        week_dates=_week_dates("2026-05-04"),
        daily_event_counts=[0, 0, 0, 1, 0, 0, 0],
        historical_avg=10.0,
        region="CN",
    )
    assert ctx.template == "slow_rhythm"
    assert ctx.is_low_activity is True
    assert "疑似周期性假期" in ctx.holiday_names


def test_read_marked_holiday_from_json(tmp_path: Path) -> None:
    path = tmp_path / "marked-holidays.json"
    path.write_text(
        json.dumps({"2026-W19": {"reason": "家庭婚礼", "holiday_names": ["家庭婚礼"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    marked = read_marked_holiday("2026-W19", path=path)
    assert isinstance(marked, dict)
    assert marked.get("reason") == "家庭婚礼"


def test_compute_five_dimensions_developer_profile() -> None:
    daily_summaries = [
        {
            "date": "2026-05-04",
            "content_full": "今天和 Claude 协作。[DECISION: 决定拆分接口] [SHIPPED: commit +12 -3 PR test]",
            "clusters": [
                {"slug": "alpha", "narrative_one_line": "alpha [SHIPPED: commit +8 -2 issue]", "event_count": 2},
            ],
            "topic_status_snapshot": {
                "alpha": {"name": "Alpha", "state": "started", "last_seen_date": "2026-05-04", "evidence_dates": ["2026-05-04"]},
            },
        },
        {
            "date": "2026-05-05",
            "content_full": "继续和 Codex 协作。",
            "clusters": [
                {"slug": "alpha", "narrative_one_line": "alpha day2", "event_count": 1},
                {"slug": "beta", "narrative_one_line": "beta started", "event_count": 1},
            ],
            "topic_status_snapshot": {
                "alpha": {"name": "Alpha", "state": "in_progress", "last_seen_date": "2026-05-05", "evidence_dates": ["2026-05-05"]},
                "beta": {"name": "Beta", "state": "started", "last_seen_date": "2026-05-05", "evidence_dates": ["2026-05-05"]},
            },
        },
    ]
    previous_week_snapshot = {"alpha": {"name": "Alpha", "state": "in_progress"}}
    dims = compute_five_dimensions(daily_summaries, previous_week_snapshot, {"work_type": "developer"})
    assert dims.decisions >= 1
    assert dims.advances >= 2
    assert "Beta" in dims.new_starts
    assert dims.collaborations["claude"] >= 1
    assert dims.collaborations["codex"] >= 1
    assert dims.outputs["commits"] >= 1


def test_render_key_data_section_exec_only() -> None:
    dims = FiveDimensions(
        decisions=5,
        advances=4,
        new_starts=["KP-Weekly-V3", "KP-Onboarding"],
        collaborations={"claude": 18, "codex": 12, "chatgpt": 0, "human_msg": 0},
        outputs={"commits": 12, "tests": 8, "lines_added": 1240, "lines_deleted": 340, "prs": 3, "issues": 2},
    )
    section = render_key_data_section(dims, "exec")
    assert "## 关键数据" in section
    assert section.count("\n|") >= 6
    assert render_key_data_section(dims, "plain") == ""


def test_failure_narrative_patterns_and_anchor_rule() -> None:
    bad = "虽然遇到困难但仍然推进了"
    markdown = "\n".join(
        [
            "## TL;DR",
            bad,
            "",
            "## 关键数据",
            "| 维度 | 数值 |",
            "|---|---|",
            "| 决策 | 1 次 |",
            "| 推进 | 1 topic-days |",
            "| 新启动 | 无 |",
            "| 协作 | 无 |",
            "| 产出 | 1 commits |",
            "",
            "## 本周关键进展",
            "### A",
            "[[2026-05-04]] [[2026-05-05]] 这是定调。",
            "→ 这意味着: 继续",
            "→ 关键决策: 决定推进",
            "→ 可见产出: commit",
            "### B",
            "[[2026-05-04]] [[2026-05-05]] 这是定调。",
            "→ 这意味着: 继续",
            "→ 关键决策: 决定推进",
            "→ 可见产出: commit",
            "### C",
            "[[2026-05-04]] [[2026-05-05]] 这是定调。",
            "→ 这意味着: 继续",
            "→ 关键决策: 决定推进",
            "→ 可见产出: commit",
            "",
            "## 本周风险",
            "| # | 风险 | 影响 | 处理方向 |",
            "|---|---|---|---|",
            "| 1 | r | i | d |",
            "",
            "## 没接住的球",
            "- [[2026-05-04]] 没继续",
            "",
            "## 一个观察",
            "最近连续 3 天都在同一主题，是否该提前重构？",
            "",
            "## 下周锚点",
            "- x",
            "",
            "## 生成信息",
            "- x",
        ]
    )
    failures = validate_weekly_output(
        style="exec",
        rendered_markdown=markdown,
        dailies_corpus=markdown,
        hud_input_dates=["2026-05-04", "2026-05-05"],
    )
    assert any(item.rule == "failure_narrative_antipattern" for item in failures)
    assert check_failure_narrative_has_anchor("卡住 3 天，今天才在 W19 解决") is True


def test_judgment_sentence_not_checked_as_objective_claim() -> None:
    markdown = "\n".join(
        [
            "## 这周的主线",
            "",
            "### 主线 A",
            "[[2026-05-04]] 这次改动把路径缩短了。[[2026-05-05]] 这是定调。",
            "→ 关键决策: 决定推进",
            "→ 可见产出: 完成发布",
            "",
            "## 这周的回声",
            "",
            "### 没接住的球",
            "- [[2026-05-04]] 没继续",
            "",
            "### 一个观察",
            "最近连续 3 天都在同一主题，是否该提前重构？",
            "",
            "> [!note] 我的批注",
            "> (空)",
        ]
    )
    failures = validate_weekly_output(
        style="plain",
        rendered_markdown=markdown,
        dailies_corpus=markdown,
        hud_input_dates=["2026-05-04", "2026-05-05"],
    )
    assert not any(item.rule == "claim_unverified" for item in failures)


def test_w19_golden_plain_and_exec_regression() -> None:
    plain = Path("docs/golden-weekly/2026-W19-plain.md").read_text(encoding="utf-8")
    exec_md = Path("docs/golden-weekly/2026-W19-exec.md").read_text(encoding="utf-8")
    plain_failures = validate_weekly_output(
        style="plain",
        rendered_markdown=plain,
        dailies_corpus=plain,
        hud_input_dates=["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08"],
    )
    exec_failures = validate_weekly_output(
        style="exec",
        rendered_markdown=exec_md,
        dailies_corpus=exec_md,
        hud_input_dates=["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08"],
    )
    assert plain_failures == []
    assert exec_failures == []


def test_run_weekly_chain_with_m2_metrics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_weekly_topics(tmp_path)
    _seed_weekly_daily_summaries()
    profile_dir = tmp_path / ".keypulse"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.joinpath("profile.toml").write_text(
        "\n".join(
            [
                "[user]",
                'work_type = "developer"',
                'region = "CN"',
                'work_mode = "flexible"',
                'weekly_trigger = "fri-pm"',
                'weekly_style = "exec"',
                'religion = ""',
                'created_at = "2026-05-01T00:00:00Z"',
                "",
                "[holidays]",
                "follow_strict = false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")
    weekly_path = run_weekly("2026-W19", style="exec")
    body = Path(weekly_path).read_text(encoding="utf-8")
    assert "## 关键数据" in body
    assert re.search(r"\| 决策 \| [1-9]\d* 次 \|", body)
    close()
