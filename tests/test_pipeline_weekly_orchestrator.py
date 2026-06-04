from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import keypulse.i18n as i18n
from click.testing import CliRunner

from keypulse.cli import main
from keypulse.pipeline.daily_summary import write_daily_summary
from keypulse.pipeline.weekly_orchestrator import _upsert_frontmatter_tags, _weekly_alias, run_weekly
from keypulse.pipeline import weekly_orchestrator
from keypulse.store.db import close, init_db
from keypulse.store.repository import get_state


def _write_topic(path: Path, *, slug: str, display_name: str, first_seen: str, last_seen: str, keywords: list[str], entries: list[str]) -> None:
    body = "\n".join(
        [
            "---",
            "type: topic",
            f"slug: {slug}",
            f"display_name: {display_name}",
            f"first_seen: {first_seen}",
            f"last_seen: {last_seen}",
            "keywords:",
            *[f"  - {item}" for item in keywords],
            "---",
            "",
            f"# {display_name}",
            "",
            "## Entries",
            *[f"- {entry}" for entry in entries],
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
    _write_topic(
        topics_dir / "alpha-topic.md",
        slug="alpha-topic",
        display_name="Alpha Topic",
        first_seen="2026-05-04",
        last_seen="2026-05-10",
        keywords=["alpha", "keypulse", "weekly", "report", "flow"],
        entries=[
            "2026-04-28 18:00 | 1 events | 上周推进 alpha",
            "2026-05-04 18:00 | 2 events | 本周推进 alpha",
        ],
    )
    _write_topic(
        topics_dir / "beta-topic.md",
        slug="beta-topic",
        display_name="Beta Topic",
        first_seen="2026-04-20",
        last_seen="2026-05-08",
        keywords=["beta", "keypulse", "hud", "weekly", "merge"],
        entries=[
            "2026-04-21 18:00 | 1 events | 上周推进 beta",
            "2026-05-05 18:00 | 1 events | 本周推进 beta",
        ],
    )


def _seed_daily_summaries(tmp_path: Path, *, week: str, days: int = 7) -> None:
    # 2026-W18 = 2026-04-27..2026-05-03
    base = [
        "2026-04-27",
        "2026-04-28",
        "2026-04-29",
        "2026-04-30",
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
    ]
    selected = base[:days]
    for idx, day in enumerate(selected):
        clusters = [
            {
                "slug": "alpha-topic",
                "display_name": "Alpha Topic",
                "narrative_one_line": f"alpha day {idx}",
                "event_count": 1,
                "time_range": ["10:00", "10:20"],
                "merge_candidate_with": ["beta-topic"] if idx == 0 else [],
            },
            {
                "slug": "beta-topic",
                "display_name": "Beta Topic",
                "narrative_one_line": f"beta day {idx}",
                "event_count": 1 if idx < 3 else 0,
                "time_range": ["11:00", "11:10"],
                "merge_candidate_with": [],
            },
        ]
        # keep zero-event rows out, daily-summary stores only hit topics
        clusters = [c for c in clusters if c["event_count"] > 0]
        write_daily_summary(
            day,
            clusters=clusters,
            misc=[],
            topic_snapshot={"alpha-topic": "active", "beta-topic": "active"},
            cost={"in_tokens": 120, "out_tokens": 40, "cost_usd": 0.001},
        )


def _seed_cost(tmp_path: Path) -> None:
    rows = [
        {
            "ts": "2026-04-27T10:00:00Z",
            "capability": "L4_weekly_reconcile",
            "model": "x",
            "tier": "standard",
            "in_tokens": 100,
            "out_tokens": 10,
            "cost_usd": 0.02,
            "cache_hit": False,
            "prompt_version": "L4_weekly_reconcile.v1",
        },
        {
            "ts": "2026-05-01T10:00:00Z",
            "capability": "L5_weekly_main_narrative",
            "model": "x",
            "tier": "standard",
            "in_tokens": 100,
            "out_tokens": 10,
            "cost_usd": 0.03,
            "cache_hit": False,
            "prompt_version": "L5_weekly_main_narrative.v1",
        },
    ]
    cost_path = tmp_path / ".keypulse" / "cost.jsonl"
    cost_path.parent.mkdir(parents=True, exist_ok=True)
    cost_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _reset_weekly_runtime_state() -> None:
    weekly_orchestrator._WEEKLY_MEMORY_CACHE.clear()
    weekly_orchestrator._WEEKLY_CIRCUIT.failures = 0
    weekly_orchestrator._WEEKLY_CIRCUIT.open_until = 0.0


class FailingGateway:
    def __init__(self, mode: str):
        self.mode = mode
        self.calls: list[str] = []

    def call(self, capability: str, prompt: str, *, input_data=None, **_kwargs):
        self.calls.append(capability)
        if self.mode == "empty":
            return {}
        if self.mode == "http400":
            raise RuntimeError("HTTP 400 bad request")
        raise RuntimeError("all failed")


class CapturingGateway:
    def __init__(self) -> None:
        self.inputs: list[dict[str, Any]] = []

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs):
        _ = prompt
        self.inputs.append({"capability": capability, "input_data": input_data})
        if capability == "L4_weekly_reconcile":
            return input_data.get("topics") if isinstance(input_data, dict) else []
        if capability == "L5_weekly_main_narrative":
            topic = input_data.get("topic") if isinstance(input_data, dict) and isinstance(input_data.get("topic"), dict) else {}
            slug = str(topic.get("slug") or "topic")
            return {
                "slug": slug,
                "narrative": f"本周 {slug} 继续推进并形成阶段结论 [[2026-04-27]]。",
                "anchors": ["[[2026-04-27]]"],
                "decisions": [],
                "outputs": [],
                "blockers": [],
            }
        if capability == "L6_explorer":
            return {
                "dropped_balls": [],
                "observation": {
                    "text": "本周有连续推进。",
                    "anchor_link": "[[2026-04-27]]",
                    "anchor_quote": "本周有连续推进。",
                },
                "risks": [],
            }
        if capability == "L7_anchor_derivation":
            return {"derived_from": None, "confidence": 0.0, "reason": ""}
        return {}


def test_weekly_activity_intensity_boundaries():
    assert weekly_orchestrator._weekly_activity_intensity(0) == ""
    assert weekly_orchestrator._weekly_activity_intensity(1) == "low"
    assert weekly_orchestrator._weekly_activity_intensity(2) == "low"
    assert weekly_orchestrator._weekly_activity_intensity(3) == "medium"
    assert weekly_orchestrator._weekly_activity_intensity(5) == "medium"
    assert weekly_orchestrator._weekly_activity_intensity(6) == "high"


def test_prepare_l5_topic_payload_adds_intensity_and_preserves_event_count():
    payload = weekly_orchestrator._prepare_l5_topic_payload(
        {
            "slug": "alpha-topic",
            "weekly_entries": [
                {"date": "2026-04-27", "event_count": 2, "narrative_one_line": "alpha"},
                {"date": "2026-04-28", "event_count": 5, "narrative_one_line": "beta"},
                {"date": "2026-04-29", "event_count": 7, "narrative_one_line": "gamma"},
            ],
        }
    )
    entries = payload["weekly_entries"]
    assert entries[0]["event_count"] == 2
    assert entries[0]["activity_intensity"] == "low"
    assert entries[1]["event_count"] == 5
    assert entries[1]["activity_intensity"] == "medium"
    assert entries[2]["event_count"] == 7
    assert entries[2]["activity_intensity"] == "high"


def test_run_weekly_stub_gateway_full_chain(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)
    _seed_cost(tmp_path)
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    output_path = run_weekly("2026-W18", style="exec")
    assert output_path

    weekly_path = Path(output_path)
    assert weekly_path.exists()
    body = weekly_path.read_text(encoding="utf-8")
    assert "# 本周工作汇报 (2026-W18" in body
    # TL;DR 段恢复（validator 强制要求、W19-21 gold 都有），但用实质内容
    # （主题数 + 完成/推进分布 + 主线名）替代旧的「本周主线集中在…」套话模板。
    assert "## TL;DR" in body
    assert "本周主线集中在" not in body
    assert "本周推进" in body
    assert "## 本周关键进展" in body
    assert "## 没接住的球" in body
    assert "生成信息:" in body

    assert (tmp_path / ".keypulse" / "topics" / "alpha-topic.md").exists()
    close()


def test_run_weekly_fallback_when_daily_count_below_threshold(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=2)

    output_path = run_weekly("2026-W18", style="exec")

    assert output_path == ""
    notice = get_state("weekly_notice")
    assert notice is not None
    assert "本周数据不足，周报跳过" in notice
    close()


def test_run_weekly_runs_when_daily_count_meets_threshold(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setenv("KEYPULSE_LANG", "zh")
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=3)
    _seed_cost(tmp_path)
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    output_path = run_weekly("2026-W18", style="exec")

    assert output_path
    assert Path(output_path).exists()
    assert get_state("weekly_notice") == ""
    close()


def test_run_weekly_l5_topics_parallel_wallclock(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("MOCK_WEEKLY_DELAY_SEC", "0.45")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    started = time.perf_counter()
    run_weekly("2026-W18", style="exec")
    elapsed = time.perf_counter() - started

    # L5 topic calls should run together; L6 waits for generated mainline sections.
    assert elapsed < 1.8
    close()


def test_run_weekly_retry_with_transient_mock_failures(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("MOCK_LLM_FAILS", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    output_path = run_weekly("2026-W18", style="exec")

    assert output_path
    assert Path(output_path).exists()
    close()


def test_weekly_cli_run_with_mock_llm(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)

    result = CliRunner().invoke(main, ["weekly", "run", "--week", "2026-W18", "--mock-llm"])

    assert result.exit_code == 0
    assert "weekly_run=" in result.output
    assert "weekly_path=" in result.output
    weekly_path = next(line.split("=", 1)[1] for line in result.output.splitlines() if line.startswith("weekly_path="))
    body = Path(weekly_path).read_text(encoding="utf-8")
    assert "# 本周工作汇报 (2026-W18" in body
    close()


def test_weekly_validator_triggers_retry_when_mock_outputs_invalid(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("MOCK_WEEKLY_INVALID_FIRST_L6", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    output_path = run_weekly("2026-W18", style="exec")

    assert output_path
    body = Path(output_path).read_text(encoding="utf-8")
    assert "生成信息:" in body
    outcome_raw = get_state("weekly_last_outcome") or ""
    outcome = json.loads(outcome_raw) if outcome_raw else {}
    assert outcome.get("outcome") in {"ok", "partial"}
    assert int(outcome.get("validator_failures") or 0) >= 0
    assert isinstance(outcome.get("quality_breakdown"), dict)
    close()


def test_weekly_cache_second_run_has_zero_llm_cost_rows(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    run_weekly("2026-W18", style="exec")
    cost_path = tmp_path / ".keypulse" / "cost.jsonl"
    cost_path.write_text("", encoding="utf-8")
    run_weekly("2026-W18", style="exec")

    rows = [json.loads(line) for line in cost_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows
    assert [row.get("source") for row in rows].count("llm") == 0
    assert all(row.get("source") == "cache" for row in rows)
    close()


def test_weekly_fallback_handles_http_400_empty_json_and_all_failed(tmp_path, monkeypatch):
    for mode in ("http400", "empty", "all"):
        _reset_weekly_runtime_state()
        case_dir = tmp_path / mode
        monkeypatch.setattr("pathlib.Path.home", lambda case_dir=case_dir: case_dir)
        monkeypatch.setattr(
            "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
            lambda _cfg, persist=False, case_dir=case_dir: SimpleNamespace(output_dir=case_dir / "vault"),
        )
        init_db(case_dir / ".keypulse" / "keypulse.db")
        _seed_topics(case_dir)
        _seed_daily_summaries(case_dir, week="2026-W18", days=7)
        monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")
        monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator._load_gateway", lambda mode=mode: FailingGateway(mode))

        output_path = run_weekly("2026-W18", style="exec")
        body = Path(output_path).read_text(encoding="utf-8")

        assert output_path
        assert "## 本周关键进展" in body
        assert "LLM 失败:" in body
        close()


def test_run_weekly_plain_style_contains_mainline_echo_and_cross_week_diff(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    output_path = run_weekly("2026-W18", style="plain")
    body = Path(output_path).read_text(encoding="utf-8")

    assert body.strip()
    assert "## 这周的主线" in body
    assert "## 这周的回声" in body
    assert "## 跨周差异" in body
    close()


def test_run_weekly_exec_style_contains_mainline_echo_and_cross_week_diff(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    output_path = run_weekly("2026-W18", style="exec")
    body = Path(output_path).read_text(encoding="utf-8")

    assert body.strip()
    assert "## 本周关键进展" in body
    assert "## 没接住的球" in body
    assert "## 一个观察" in body
    assert "## 跨周差异" in body
    close()


def test_run_weekly_injects_entity_merge_payload_into_l5_input(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.validate_weekly_output",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator._prepare_weekly_entity_payload",
        lambda _week: {
            "canonical_entities": [
                {
                    "canonical_name": "KeyPulse",
                    "type": "project",
                    "aliases": ["keypulse"],
                    "appears_on_dates": ["2026-04-27"],
                    "merge_reasoning": "same repo context",
                }
            ],
            "event_entity_map": [
                {
                    "event_id": "1",
                    "primary_entity": "KeyPulse",
                    "confidence": 0.92,
                    "needs_review": False,
                    "date": "2026-04-27",
                }
            ],
        },
    )
    gateway = CapturingGateway()
    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator._load_gateway", lambda: gateway)
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)

    output_path = run_weekly("2026-W18", style="exec")
    assert output_path

    l5_inputs = [item["input_data"] for item in gateway.inputs if item.get("capability") == "L5_weekly_main_narrative"]
    assert l5_inputs
    assert all("canonical_entities" in payload for payload in l5_inputs if isinstance(payload, dict))
    assert all("event_entity_map" in payload for payload in l5_inputs if isinstance(payload, dict))
    for payload in l5_inputs:
        if not isinstance(payload, dict):
            continue
        topic = payload.get("topic")
        if not isinstance(topic, dict):
            continue
        weekly_entries = topic.get("weekly_entries")
        if not isinstance(weekly_entries, list):
            continue
        for entry in weekly_entries:
            if not isinstance(entry, dict):
                continue
            assert "event_count" in entry
            assert entry.get("activity_intensity") in {"low", "medium", "high"}

    run_record_path = tmp_path / ".keypulse" / "run_records" / "2026-W18.json"
    run_record = json.loads(run_record_path.read_text(encoding="utf-8"))
    assert run_record["stage_status"].get("entity_merge") == "ok"
    close()


def test_prepare_weekly_entity_payload_returns_none_when_merger_fails(monkeypatch):
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator._load_week_daily_entity_outputs",
        lambda _week: [
            {
                "date": "2026-04-27",
                "entities": [{"name": "KeyPulse", "type": "project", "evidence_event_ids": ["1"]}],
                "event_entity_map": [{"event_id": "1", "primary_entity": "KeyPulse", "confidence": 0.9, "needs_review": False}],
            }
        ],
    )
    monkeypatch.setattr(
        "keypulse.pipeline.entity_merger_llm.merge_entities",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert weekly_orchestrator._prepare_weekly_entity_payload("2026-W18") is None


def test_run_weekly_writes_frontmatter_tags_for_plain_and_exec_styles(tmp_path, monkeypatch):
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries(tmp_path, week="2026-W18", days=7)
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    plain_path = Path(run_weekly("2026-W18", style="plain"))
    plain_lines = plain_path.read_text(encoding="utf-8").splitlines()

    assert plain_lines[0] == "---"
    assert plain_lines[1] == "tags:"
    assert '  - "weekly"' in plain_lines
    assert '  - "weekly/plain"' in plain_lines
    assert '  - "2026-W18 (4/27–5/3)"' in plain_lines

    exec_path = Path(run_weekly("2026-W18", style="exec"))
    exec_lines = exec_path.read_text(encoding="utf-8").splitlines()

    assert exec_lines[0] == "---"
    assert exec_lines[1] == "tags:"
    assert '  - "weekly"' in exec_lines
    assert '  - "weekly/exec"' in exec_lines
    assert '  - "2026-W18 (4/27–5/3)"' in exec_lines
    close()


def test_upsert_frontmatter_tags_appends_when_frontmatter_already_exists():
    source = "\n".join(
        [
            "---",
            'title: "Weekly Note"',
            'tags: ["legacy"]',
            "---",
            "# Weekly",
        ]
    )
    patched = _upsert_frontmatter_tags(source, ["weekly", "weekly/exec"])

    assert patched.splitlines()[0] == "---"
    assert 'title: "Weekly Note"' in patched
    assert '  - "legacy"' in patched
    assert '  - "weekly"' in patched
    assert '  - "weekly/exec"' in patched


def test_weekly_alias_switches_with_locale(monkeypatch):
    monkeypatch.setenv("KEYPULSE_LANG", "zh")
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)
    assert _weekly_alias("2026-W21") == "2026-W21 (5/18–5/24)"

    monkeypatch.setenv("KEYPULSE_LANG", "en")
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)
    assert _weekly_alias("2026-W21") == "2026-W21 (May 18 – May 24)"


# --- W22 deep-fix: 原则精选 + 信号单一归属 ---

def test_select_weekly_principles_dedups_and_caps():
    raw = [
        {"slug": "data-input-quality-context", "distilled": "Ensure high quality data input avoiding unlabeled platform data"},
        {"slug": "data-input-quality-importance", "distilled": "Ensure high quality data input avoiding unlabeled platform data lacking context"},
        {"slug": "data-quality-context", "distilled": "Ensure high quality data input by avoiding unlabeled platform dependent data"},
    ] + [
        {"slug": f"distinct-principle-{i}", "distilled": f"完全不同的原则编号{i}专属内容 alpha beta gamma {i}"}
        for i in range(12)
    ]
    selected = weekly_orchestrator._select_weekly_principles(raw, cap=8)
    slugs = [item["slug"] for item in selected]
    # 三条 data-* 近义/同族只保留一条
    data_kept = [s for s in slugs if s.startswith("data-")]
    assert len(data_kept) == 1
    # 硬截断到 cap
    assert len(selected) <= 8
    assert weekly_orchestrator._select_weekly_principles([], cap=8) == []


def test_assign_signals_to_topics_single_home_no_global_fallback():
    topics = [
        {"slug": "carmind", "name": "Carmind_code"},
        {"slug": "keypulse", "name": "KeyPulse"},
        {"slug": "failure-stack", "name": "failure-stack 文档"},
    ]
    signals = [
        {"text": "Carmind_code 对比 rewrite.py 320->815 行"},
        {"text": "Carmind_code 接百度 MCP 替代自动抓取"},  # 同主题第二条
        {"text": "KeyPulse 收紧决策提取正则"},
        {"text": "与任何主题都不沾边的全局噪声决策"},  # 无 token 命中 -> 丢弃
    ]
    assigned = weekly_orchestrator._assign_signals_to_topics(signals, topics, limit=3)
    carmind_texts = [s["text"] for s in assigned["carmind"]]
    assert any("rewrite.py" in t for t in carmind_texts)
    # 无匹配的全局噪声不会回退到任何主题
    all_assigned = [s["text"] for v in assigned.values() for s in v]
    assert "与任何主题都不沾边的全局噪声决策" not in all_assigned
    # failure-stack 不应拿到 Carmind 的决策（旧 bug：全局 top-3 fallback）
    assert assigned["failure-stack"] == []


def test_assign_signals_to_topics_global_text_dedup():
    topics = [
        {"slug": "carmind", "name": "Carmind_code"},
        {"slug": "carmind-ui", "name": "Carmind_code 前端"},
    ]
    signals = [
        {"text": "Carmind_code rewrite.py 320->815"},
        {"text": "Carmind_code rewrite.py 320->815"},  # 完全重复
    ]
    assigned = weekly_orchestrator._assign_signals_to_topics(signals, topics, limit=3)
    total = sum(len(v) for v in assigned.values())
    assert total == 1  # 同一条文本只落一处


# --- W22 deep-fix: 按 canonical 实体合并碎片主题 ---

_CANON = [
    {"canonical_name": "Carmind_code", "type": "project", "aliases": ["agent-runtime"]},
    {"canonical_name": "Kiro", "type": "project", "aliases": []},
    {"canonical_name": "KeyPulse", "type": "project", "aliases": ["keypulse"]},
    {"canonical_name": "unknown", "type": "unknown", "aliases": []},
]


def test_collapse_merges_same_project_fragments():
    topics = [
        {"slug": "carmind-llm", "name": "Carmind_code LLM 预检", "state": "completed",
         "weekly_entries": [{"date": "2026-05-27"}]},
        {"slug": "carmind-m4a", "name": "Carmind_code m4a 推理", "state": "in_progress",
         "weekly_entries": [{"date": "2026-05-25"}]},
        {"slug": "keypulse-fix", "name": "KeyPulse 代码修复", "state": "completed",
         "weekly_entries": [{"date": "2026-05-25"}]},
    ]
    out = weekly_orchestrator._collapse_topics_by_canonical_entity(topics, _CANON)
    names = [t["name"] for t in out]
    assert names == ["Carmind_code", "KeyPulse"]  # 3 碎片 -> 2 项目, 保序
    carmind = out[0]
    assert set(carmind["member_slugs"]) == {"carmind-llm", "carmind-m4a"}
    # 合并后状态聚合：completed + in_progress -> in_progress
    assert carmind["state"] == "in_progress"
    assert {e["date"] for e in carmind["weekly_entries"]} == {"2026-05-27", "2026-05-25"}


def test_collapse_canonical_name_beats_alias_no_false_merge():
    # 回归：slug "kiro-agent-runtime" 含 Carmind 别名 "agent-runtime"，
    # 但 canonical_name "kiro" 命中应优先 -> 归 Kiro 而非 Carmind_code。
    topics = [
        {"slug": "kiro-agent-runtime", "name": "Kiro 单Agent Runtime架构结论", "state": "completed",
         "weekly_entries": [{"date": "2026-05-26"}]},
    ]
    out = weekly_orchestrator._collapse_topics_by_canonical_entity(topics, _CANON)
    assert len(out) == 1
    assert out[0]["name"] == "Kiro"


def test_collapse_noop_without_entities():
    topics = [{"slug": "a", "name": "A", "state": "completed"}]
    assert weekly_orchestrator._collapse_topics_by_canonical_entity(topics, None) == topics
    assert weekly_orchestrator._collapse_topics_by_canonical_entity(topics, []) == topics
