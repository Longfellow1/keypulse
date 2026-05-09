from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from keypulse.pipeline.daily_summary import write_daily_summary
from keypulse.pipeline.weekly_orchestrator import run_weekly
from keypulse.pipeline import weekly_orchestrator
from keypulse.store.db import close, init_db


class GatewayByMode:
    def __init__(self, mode: str):
        self.mode = mode

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs):
        _ = capability
        _ = prompt
        _ = input_data
        if self.mode == "http400":
            raise RuntimeError("HTTP 400 bad request")
        if self.mode == "empty":
            return {}
        if self.mode == "all_failed":
            raise RuntimeError("all retries failed")
        raise RuntimeError("unknown mode")


def _seed_topics(tmp_path: Path) -> None:
    topics_dir = tmp_path / ".keypulse" / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    (topics_dir / "alpha-topic.md").write_text(
        "\n".join(
            [
                "---",
                "type: topic",
                "slug: alpha-topic",
                "display_name: Alpha Topic",
                "first_seen: 2026-04-20",
                "last_seen: 2026-05-09",
                "keywords:",
                "  - alpha",
                "  - keypulse",
                "---",
                "",
                "# Alpha Topic",
                "",
                "## Entries",
                "- 2026-04-29 18:00 | 1 events | alpha history",
                "",
                "## Related Events",
                "- [[../.keypulse/events/2026-05-04/1|1]]",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _seed_daily_summaries() -> None:
    for day in ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01", "2026-05-02", "2026-05-03"):
        write_daily_summary(
            day,
            clusters=[
                {
                    "slug": "alpha-topic",
                    "display_name": "Alpha Topic",
                    "narrative_one_line": f"{day} alpha",
                    "event_count": 1,
                    "time_range": ["10:00", "10:05"],
                    "merge_candidate_with": [],
                }
            ],
            misc=[],
            topic_snapshot={
                "alpha-topic": {
                    "name": "Alpha Topic",
                    "state": "in_progress",
                    "last_seen_date": day,
                    "evidence_dates": [day],
                }
            },
            cost={"in_tokens": 1, "out_tokens": 1, "cost_usd": 0.0},
        )


def _reset_runtime_state() -> None:
    weekly_orchestrator._WEEKLY_MEMORY_CACHE.clear()
    weekly_orchestrator._WEEKLY_CIRCUIT.failures = 0
    weekly_orchestrator._WEEKLY_CIRCUIT.open_until = 0.0


def _prepare(tmp_path: Path, monkeypatch) -> None:
    _reset_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_topics(tmp_path)
    _seed_daily_summaries()


def test_weekly_reliability_http_400_falls_back_without_crash(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator._load_gateway", lambda: GatewayByMode("http400"))
    output_path = run_weekly("2026-W18", style="exec")
    body = Path(output_path).read_text(encoding="utf-8")
    assert output_path
    assert "## 本周关键进展" in body
    close()


def test_weekly_reliability_empty_json_falls_back_without_crash(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator._load_gateway", lambda: GatewayByMode("empty"))
    output_path = run_weekly("2026-W18", style="exec")
    body = Path(output_path).read_text(encoding="utf-8")
    assert output_path
    assert "## 本周关键进展" in body
    close()


def test_weekly_reliability_all_failed_returns_partial_not_exception(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator._load_gateway", lambda: GatewayByMode("all_failed"))
    output_path = run_weekly("2026-W18", style="exec")
    body = Path(output_path).read_text(encoding="utf-8")
    assert output_path
    assert "LLM 失败:" in body
    close()


def test_weekly_reliability_cache_hit_skips_failing_gateway(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    monkeypatch.setenv("MOCK_LLM", "1")
    run_weekly("2026-W18", style="exec")
    monkeypatch.delenv("MOCK_LLM", raising=False)

    cost_path = tmp_path / ".keypulse" / "cost.jsonl"
    cost_path.write_text("", encoding="utf-8")
    monkeypatch.setattr("keypulse.pipeline.weekly_orchestrator._load_gateway", lambda: GatewayByMode("all_failed"))
    output_path = run_weekly("2026-W18", style="exec")
    body = Path(output_path).read_text(encoding="utf-8")

    rows = [json.loads(line) for line in cost_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert output_path
    assert "## 本周关键进展" in body
    assert rows
    assert all(row.get("source") == "cache" for row in rows)
    close()
