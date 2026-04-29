from __future__ import annotations

from pathlib import Path

from keypulse.obsidian.quality_gate import QualityScore, evaluate, score_daily, should_write_daily


def _healthy_daily(thing_count: int = 12, words_per_thing: int = 120) -> str:
    sections: list[str] = ["# 2026-04-28", ""]
    for idx in range(thing_count):
        tokens = " ".join(f"focus_{idx}_{word}" for word in range(words_per_thing))
        sections.extend(
            [
                f"### 事项{idx + 1}",
                f"你在今天处理第{idx + 1}段工作，{tokens}，并把结果写入记录。",
                "",
            ]
        )
    return "\n".join(sections)


def _short_daily(thing_count: int = 3, words_per_thing: int = 55) -> str:
    sections: list[str] = ["# 2026-04-28", ""]
    for idx in range(thing_count):
        tokens = " ".join(f"mini_{idx}_{word}" for word in range(words_per_thing))
        sections.extend(
            [
                f"### 短事项{idx + 1}",
                f"这段记录用于回归校验，包含 {tokens}，用于比较长度阈值。",
                "",
            ]
        )
    return "\n".join(sections)


def test_score_daily_empty() -> None:
    score = score_daily("")
    assert score.thing_count == 0
    assert score.total_chars == 0
    assert score.template_density == 0.0
    assert score.unique_word_ratio == 0.0


def test_score_daily_healthy() -> None:
    text = _healthy_daily()
    score = score_daily(text)
    assert score.thing_count == 12
    assert score.total_chars > 9000
    assert score.template_density < 0.15
    assert score.unique_word_ratio > 0.4


def test_score_daily_template_heavy() -> None:
    lines = ["# 2026-04-28", ""]
    for idx in range(10):
        lines.extend(
            [
                f"### 模板段{idx + 1}",
                f"10:{idx:02d}-10:{idx:02d} 在 Chrome 做 task_{idx}",
                "做了一些操作，看不出方向",
                "",
            ]
        )
    score = score_daily("\n".join(lines))
    assert score.thing_count == 10
    assert score.template_density > 0.15


_HEALTHY_BASELINE = QualityScore(thing_count=12, total_chars=10000, template_density=0.0, unique_word_ratio=0.7)


def test_evaluate_bootstrap_passes_without_baseline() -> None:
    ok, reason = evaluate(QualityScore(thing_count=1, total_chars=300, template_density=0.0, unique_word_ratio=0.5), None)
    assert ok is True
    assert reason == "bootstrap"


def test_evaluate_bootstrap_rejects_empty_content() -> None:
    ok, reason = evaluate(QualityScore(thing_count=0, total_chars=0, template_density=0.0, unique_word_ratio=0.0), None)
    assert ok is False
    assert reason == "empty new content"


def test_evaluate_thing_count_too_few() -> None:
    ok, reason = evaluate(QualityScore(thing_count=2, total_chars=5000, template_density=0.0, unique_word_ratio=0.8), _HEALTHY_BASELINE)
    assert ok is False
    assert reason == "thing_count=2<3"


def test_evaluate_template_density_too_high() -> None:
    ok, reason = evaluate(QualityScore(thing_count=6, total_chars=5000, template_density=0.2, unique_word_ratio=0.8), _HEALTHY_BASELINE)
    assert ok is False
    assert reason == "template_density=0.20>0.15"


def test_evaluate_unique_ratio_too_low() -> None:
    ok, reason = evaluate(QualityScore(thing_count=6, total_chars=5000, template_density=0.0, unique_word_ratio=0.2), _HEALTHY_BASELINE)
    assert ok is False
    assert reason == "unique_word_ratio=0.20<0.4"


def test_evaluate_chars_below_baseline_floor() -> None:
    baseline = QualityScore(thing_count=12, total_chars=11000, template_density=0.0, unique_word_ratio=0.7)
    new_score = QualityScore(thing_count=4, total_chars=1300, template_density=0.0, unique_word_ratio=0.7)
    ok, reason = evaluate(new_score, baseline)
    assert ok is False
    assert reason == "total_chars=1300 < baseline 11000*0.6"


def test_evaluate_chars_above_baseline_floor() -> None:
    baseline = QualityScore(thing_count=12, total_chars=10000, template_density=0.0, unique_word_ratio=0.7)
    new_score = QualityScore(thing_count=4, total_chars=7000, template_density=0.0, unique_word_ratio=0.7)
    ok, reason = evaluate(new_score, baseline)
    assert ok is True
    assert reason == "ok"


def test_should_write_daily_no_existing(tmp_path: Path) -> None:
    target = tmp_path / "Daily" / "2026-04-28.md"
    ok, reason, new_score, old_score = should_write_daily(_healthy_daily(thing_count=4, words_per_thing=80), target)
    assert ok is True
    assert reason == "bootstrap"
    assert new_score.thing_count == 4
    assert old_score is None


def test_should_write_daily_bootstrap_skeleton_passes(tmp_path: Path) -> None:
    target = tmp_path / "Daily" / "2026-04-28.md"
    skeleton = "---\ntype: daily\n---\n\n# 2026-04-28\n\n- 事件卡：1\n"
    ok, reason, new_score, old_score = should_write_daily(skeleton, target)
    assert ok is True
    assert reason == "bootstrap"
    assert new_score.thing_count == 0
    assert old_score is None


def test_should_write_daily_old_better_new_short(tmp_path: Path) -> None:
    target = tmp_path / "Daily" / "2026-04-28.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_healthy_daily(thing_count=12, words_per_thing=150), encoding="utf-8")

    new_text = _short_daily(thing_count=3, words_per_thing=75)
    ok, reason, new_score, old_score = should_write_daily(new_text, target)

    assert ok is False
    assert reason.startswith("total_chars=")
    assert new_score.total_chars < int(old_score.total_chars * 0.6)  # type: ignore[union-attr]
