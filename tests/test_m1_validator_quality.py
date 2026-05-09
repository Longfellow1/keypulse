from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from keypulse.pipeline.daily_summary import write_daily_summary
from keypulse.pipeline.degraded_content import generate_degraded_topic
from keypulse.pipeline.quality_score import append_quality_log, compute_quality_score
from keypulse.pipeline.weekly_orchestrator import _sanitize_weekly_outputs, run_weekly
from keypulse.pipeline.weekly_validator import ValidationFailure, classify_sentence, find_evidence, validate_weekly_output
from keypulse.store.db import close, init_db


def _write_topic(path: Path, *, slug: str, display_name: str) -> None:
    body = "\n".join(
        [
            "---",
            "type: topic",
            f"slug: {slug}",
            f"display_name: {display_name}",
            "first_seen: 2026-05-04",
            "last_seen: 2026-05-10",
            "keywords:",
            "  - keypulse",
            "---",
            "",
            f"# {display_name}",
            "",
            "## Entries",
            "- 2026-05-04 18:00 | 1 events | 本周推进",
            "",
            "## Related Events",
            "- [[../.keypulse/events/2026-05-04/1|1]]",
            "",
        ]
    )
    path.write_text(body, encoding="utf-8")


def _seed_topics(tmp_path: Path) -> None:
    topics_dir = tmp_path / ".keypulse" / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    _write_topic(topics_dir / "alpha-topic.md", slug="alpha-topic", display_name="Alpha Topic")


def _seed_daily_summaries() -> None:
    for idx, day in enumerate(["2026-05-04", "2026-05-05", "2026-05-06"]):
        write_daily_summary(
            day,
            clusters=[
                {
                    "slug": "alpha-topic",
                    "display_name": "Alpha Topic",
                    "narrative_one_line": f"alpha day {idx} [DECISION: 决定推进{idx}] [SHIPPED: 完成上线{idx}]",
                    "event_count": 1,
                    "time_range": ["10:00", "10:10"],
                    "merge_candidate_with": [],
                }
            ],
            misc=[],
            topic_snapshot={"alpha-topic": "active"},
            cost={"in_tokens": 10, "out_tokens": 5, "cost_usd": 0.001},
        )


def test_golden_plain_passes_validator() -> None:
    markdown = Path("docs/golden-weekly/2026-W19-plain.md").read_text(encoding="utf-8")
    failures = validate_weekly_output(
        style="plain",
        rendered_markdown=markdown,
        dailies_corpus=markdown,
        hud_input_dates=["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08"],
    )
    assert failures == []


def test_golden_exec_passes_validator() -> None:
    markdown = Path("docs/golden-weekly/2026-W19-exec.md").read_text(encoding="utf-8")
    failures = validate_weekly_output(
        style="exec",
        rendered_markdown=markdown,
        dailies_corpus=markdown,
        hud_input_dates=["2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08"],
    )
    assert failures == []


def test_empty_skeleton_fails_hard() -> None:
    markdown = "\n".join(
        [
            "## TL;DR",
            "暂无可确认的",
            "",
            "## 关键数据",
            "- 0",
            "",
            "## 本周关键进展",
            "### 本周X有连续记录",
            "本周X有连续记录",
            "",
            "## 本周风险",
            "- 无",
            "",
            "## 没接住的球",
            "- 无",
            "",
            "## 下周锚点",
            "- 无",
            "",
            "## 生成信息",
            "- 无",
        ]
    )
    failures = validate_weekly_output(
        style="exec",
        rendered_markdown=markdown,
        dailies_corpus="",
        hud_input_dates=["2026-05-04"],
    )
    assert len(failures) >= 5


def test_find_evidence_levels() -> None:
    synonyms = {"claude": ["Claude", "claude"]}
    corpus = "今天用 Claude Code 跑了 5 次"

    assert find_evidence("Claude", corpus, synonyms) == "exact"
    assert find_evidence("claude", "今天用 Claude Code", synonyms) == "synonym"
    assert find_evidence("约5次", corpus, synonyms) == "fuzzy_number"
    assert find_evidence("不存在的证据", corpus, synonyms) == "missing"


def test_classify_sentence() -> None:
    assert classify_sentence("这是定调") == "judgment"
    assert classify_sentence("周三 push 了 5 个 commit") == "fact"


def test_quality_score_deduction_rules(tmp_path: Path) -> None:
    empty = compute_quality_score([])
    assert empty.total == 100

    structure_only = compute_quality_score([ValidationFailure(field="structure", rule="section_missing", detail="x")])
    assert structure_only.total == 80

    antipattern_only = compute_quality_score([ValidationFailure(field="antipattern", rule="blacklist_hit", detail="x")])
    assert antipattern_only.total == 75

    log_path = tmp_path / "weekly-quality.jsonl"
    append_quality_log("2026-W19", empty, log_path=log_path)
    assert log_path.exists()


def test_degraded_content_generator_uses_daily_data() -> None:
    topic = {"slug": "alpha-topic", "name": "Alpha Topic", "weekly_entries": []}
    daily_summaries = [
        {
            "date": "2026-05-04",
            "content_full": "alpha-topic [DECISION: 决定收敛] [SHIPPED: 完成接通]",
            "clusters": [
                {
                    "slug": "alpha-topic",
                    "display_name": "Alpha Topic",
                    "narrative_one_line": "alpha one line",
                }
            ],
        }
    ]
    degraded = generate_degraded_topic(topic, daily_summaries, "llm_failed")
    assert "alpha one line" in str(degraded.get("narrative") or "")
    assert "本周X有连续记录" not in str(degraded.get("narrative") or "")


def test_sanitize_no_longer_clears_outputs() -> None:
    l5 = {"narratives": [{"slug": "alpha", "narrative": "非空内容"}]}
    l6 = {"observation": {"text": "非空"}, "missed_balls": [{"what": "非空"}]}
    failures = [ValidationFailure(field="coverage", rule="x", detail="x")]

    safe_l5, safe_l6 = _sanitize_weekly_outputs(l5, l6, failures)
    assert safe_l5["narratives"][0]["narrative"] == "非空内容"
    assert safe_l6["observation"]["text"] == "非空"
    assert safe_l5.get("quality_status") == "validator_failed"


def test_footer_renders_quality_details(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries()
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    output_path = run_weekly("2026-W19", style="exec")
    body = Path(output_path).read_text(encoding="utf-8")

    assert re.search(r"质量\s*\d+/100", body)
    assert "<details>" in body
    assert "质量分详情" in body

    quality_log = tmp_path / ".keypulse" / "weekly-quality.jsonl"
    rows = [json.loads(line) for line in quality_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    close()
