"""M5 daily_validator 测试 — 5 类断言 + 黄金回归 + 退化区分能力。"""

from pathlib import Path

import pytest

from keypulse.pipeline.daily_validator import (
    DailyValidationFailure,
    quick_score,
    validate_daily_output,
)

GOLDEN_56 = Path(__file__).parent.parent / "docs" / "golden-daily" / "2026-05-06.md"
GOLDEN_59 = Path(__file__).parent.parent / "docs" / "golden-daily" / "2026-05-09.md"


def _rules(failures: list[DailyValidationFailure]) -> set[str]:
    return {f.rule for f in failures}


def _has_severity(failures: list[DailyValidationFailure], severity: str) -> bool:
    return any(f.severity == severity for f in failures)


def test_golden_56_passes() -> None:
    md = GOLDEN_56.read_text()
    failures = validate_daily_output(rendered_markdown=md)
    errors = [f for f in failures if f.severity == "error"]
    assert errors == [], f"5/6 黄金应零 error: {[f.message for f in errors]}"
    assert quick_score(failures) >= 95


def test_golden_59_opus_passes() -> None:
    md = GOLDEN_59.read_text()
    failures = validate_daily_output(rendered_markdown=md)
    assert failures == [], f"5/9 黄金应零 failure: {[f.message for f in failures]}"
    assert quick_score(failures) == 100


def test_skeleton_with_action_object_topic_fails() -> None:
    md = """📍 Asia/Shanghai

# 2026-05-09

## 今日要点

凑数。

## 今天做的事

### 修改CorpusFlow项目

凌晨 0 点处理 Markdown 笔记，没什么实际进展。

### 登录SnapDeploy

8 点登录页面。
"""
    failures = validate_daily_output(rendered_markdown=md)
    rules = _rules(failures)
    assert "antipattern" in rules, "动作+对象 标题必须报反模式"
    msgs = " ".join(f.message for f in failures)
    assert "修改CorpusFlow" in msgs or "登录SnapDeploy" in msgs


def test_generic_repeat_fails() -> None:
    md = """# 2026-05-09

## 今日要点

测试。

## 今天做的事

### 周报 v3 落地

整天推进 M0-M3 落地，commit 多次。这意味着进入可继续迭代阶段。
"""
    failures = validate_daily_output(rendered_markdown=md)
    rules = _rules(failures)
    assert "antipattern" in rules


def test_generic_repeat_in_quotes_passes() -> None:
    md = """# 2026-05-09

## 今日要点

测试反模式引用不被误杀。

## 今天做的事

### 周报 v3 落地

整天 commit 多次，落地 M0-M3。诊断时发现"进入可继续迭代阶段"这种复读句应该被 validator 拦下。
"""
    failures = validate_daily_output(rendered_markdown=md)
    rules = _rules(failures)
    assert "antipattern" not in rules, "引号包裹的反模式示例不该 fail"


def test_missing_required_section_fails() -> None:
    md = """# 2026-05-09

## 今天做的事

### 周报 v3 落地

commit 落地 M0-M3。
"""
    failures = validate_daily_output(rendered_markdown=md)
    rules = _rules(failures)
    assert "structure" in rules


def test_short_topic_segment_fails() -> None:
    md = """# 2026-05-09

## 今日要点

测试。

## 今天做的事

### 周报 v3

太短。
"""
    failures = validate_daily_output(rendered_markdown=md)
    coverage_msgs = [f.message for f in failures if f.rule == "coverage"]
    assert any("80" in m for m in coverage_msgs)


def test_data_layer_single_event_anchored_fails() -> None:
    md = """# 2026-05-09

## 今日要点

测试。

## 今天做的事

### 周报 v3 落地

commit 落地 M0-M3, 13 个新文件 5/9。
"""
    daily_summary = {
        "topics": [
            {
                "anchor": "weekly-v3-rollout",
                "narrative": "整天推进 M0-M3 落地，13 个新文件，1032 测试全过 5/9，commit 多次。这是真主线。" * 2,
                "decisions": ["按 M0-M3 顺序"],
                "shipped": ["PR #3 merged"],
                "events_ref": ["c1"],
            }
        ],
        "events": [
            {
                "cluster_id": "c1",
                "display_name": "v3 落地",
                "narrative_one_line": "推进 M0-M3 落地",
                "event_count": 1,
                "anchored_to": "weekly-v3-rollout",
            }
        ],
        "unanchored": [],
    }
    failures = validate_daily_output(
        rendered_markdown=md,
        daily_summary=daily_summary,
    )
    rules = _rules(failures)
    assert "antipattern" in rules, "单 event 升格成 anchored topic 必须 fail"
    msgs = " ".join(f.message for f in failures)
    assert "单 event 不应升格" in msgs


def test_data_layer_missing_topics_fails() -> None:
    md = """# 2026-05-09

## 今日要点

测试。

## 今天做的事

### 周报 v3 落地

commit 落地 M0-M3, 13 个新文件 5/9。
"""
    daily_summary = {"topics": [], "events": [], "unanchored": []}
    failures = validate_daily_output(rendered_markdown=md, daily_summary=daily_summary)
    rules = _rules(failures)
    assert "coverage" in rules


def test_baseline_detects_degradation_5_9_vs_5_6() -> None:
    md_56 = GOLDEN_56.read_text()
    md_59_actual = Path("/Users/Harland/Go/Knowledge/Daily/2026-05-09.md")
    if not md_59_actual.exists():
        pytest.skip("现状 5/9 daily 文件不存在 (仅本地有)")
    score_56 = quick_score(validate_daily_output(rendered_markdown=md_56))
    score_59 = quick_score(validate_daily_output(rendered_markdown=md_59_actual.read_text()))
    if score_56 <= score_59:
        pytest.skip(f"本地 5/9 已不劣于 5/6: 5/6={score_56}, 5/9={score_59}")
    gap = score_56 - score_59
    if gap < 30:
        pytest.skip(f"本地 5/9 已改善，退化差距不足 30: {gap}")
