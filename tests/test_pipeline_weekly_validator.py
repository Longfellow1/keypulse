from __future__ import annotations

from keypulse.pipeline.weekly_validator import validate_weekly_output


def test_validate_main_narrative_length_bounds() -> None:
    plain_markdown = "\n".join(
        [
            "## 这周的主线",
            "",
            "### 主线 A",
            f"[[2026-05-04]] {'推进' * 600} [[2026-05-05]] 这是定调。",
            "→ 关键决策: 决定推进",
            "→ 可见产出: 完成发布",
            "",
            "## 这周的回声",
            "",
            "### 没接住的球",
            "- [[2026-05-04]] 本周没看到后续",
            "",
            "### 一个观察",
            "最近连续 3 天都在同一主题，是否该提前重构？",
            "",
            "> [!note] 我的批注",
            "> (空)",
        ]
    )
    plain_failures = validate_weekly_output(
        style="plain",
        rendered_markdown=plain_markdown,
        dailies_corpus=plain_markdown,
        hud_input_dates=["2026-05-04", "2026-05-05"],
    )
    assert not any(item.rule == "mainline_too_short" for item in plain_failures)

    exec_too_short = "\n".join(
        [
            "## TL;DR",
            "短",
            "",
            "## 关键数据",
            "- 1",
            "",
            "## 本周关键进展",
            "### A",
            "[[2026-05-04]] [[2026-05-05]] 决定推进，完成上线。",
            "→ 这意味着: 继续",
            "→ 关键决策: 决定推进",
            "→ 可见产出: 完成上线",
            "",
            "## 本周风险",
            "- 无",
            "",
            "## 没接住的球",
            "- [[2026-05-04]] 没继续",
            "",
            "## 下周锚点",
            "- A",
            "",
            "## 生成信息",
            "- x",
        ]
    )
    exec_failures = validate_weekly_output(
        style="exec",
        rendered_markdown=exec_too_short,
        dailies_corpus=exec_too_short,
        hud_input_dates=["2026-05-04", "2026-05-05"],
    )
    assert any(item.rule == "mainline_too_short" for item in exec_failures)
