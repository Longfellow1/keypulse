from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from keypulse.cli import main
from keypulse.pipeline.daily_summary import write_daily_summary
from keypulse.pipeline.weekly_orchestrator import run_weekly
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
    assert "## TL;DR" in body
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
