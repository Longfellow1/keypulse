from __future__ import annotations

import json

from click.testing import CliRunner

from keypulse.cli import main


def test_cost_command_month_and_grouping(monkeypatch, tmp_path):
    monkeypatch.setattr("keypulse.cli.get_data_dir", lambda: tmp_path)
    cost_path = tmp_path / "cost.jsonl"
    rows = [
        {
            "ts": "2026-05-02T10:00:00Z",
            "capability": "L1_cluster_review",
            "model": "deepseek-chat",
            "tier": "standard",
            "in_tokens": 100,
            "out_tokens": 20,
            "cost_usd": 0.020,
            "cache_hit": False,
            "prompt_version": "L1.v1",
        },
        {
            "ts": "2026-05-03T10:00:00Z",
            "capability": "L1_cluster_review",
            "model": "deepseek-chat",
            "tier": "standard",
            "in_tokens": 100,
            "out_tokens": 20,
            "cost_usd": 0.0,
            "cache_hit": True,
            "prompt_version": "L1.v1",
        },
        {
            "ts": "2026-05-04T10:00:00Z",
            "capability": "L2_narrative",
            "model": "qwen2.5:7b",
            "tier": "mini",
            "in_tokens": 80,
            "out_tokens": 30,
            "cost_usd": 0.010,
            "cache_hit": False,
            "prompt_version": "L2.v1",
        },
        {
            "ts": "2026-04-30T10:00:00Z",
            "capability": "L3_topic_naming",
            "model": "qwen2.5:7b",
            "tier": "mini",
            "in_tokens": 50,
            "out_tokens": 10,
            "cost_usd": 0.005,
            "cache_hit": False,
            "prompt_version": "L3.v1",
        },
    ]
    cost_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["cost", "--month", "2026-05"])

    assert result.exit_code == 0
    assert "2026-05" in result.output
    assert "$0.03" in result.output
    assert "L1_cluster_review" in result.output
    assert "L2_narrative" in result.output
    assert "cache hits" in result.output


def test_cost_command_week(monkeypatch, tmp_path):
    monkeypatch.setattr("keypulse.cli.get_data_dir", lambda: tmp_path)
    cost_path = tmp_path / "cost.jsonl"
    rows = [
        {
            "ts": "2026-05-04T10:00:00Z",  # 2026-W19
            "capability": "L1",
            "model": "m",
            "tier": "standard",
            "in_tokens": 10,
            "out_tokens": 1,
            "cost_usd": 0.011,
            "cache_hit": False,
            "prompt_version": "L1.v1",
        },
        {
            "ts": "2026-04-30T10:00:00Z",  # 2026-W18
            "capability": "L1",
            "model": "m",
            "tier": "standard",
            "in_tokens": 10,
            "out_tokens": 1,
            "cost_usd": 0.009,
            "cache_hit": False,
            "prompt_version": "L1.v1",
        },
    ]
    cost_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["cost", "--week", "2026-W18"])

    assert result.exit_code == 0
    assert "2026-W18" in result.output
    assert "$0.01" in result.output


def test_cost_command_bad_month_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr("keypulse.cli.get_data_dir", lambda: tmp_path)
    (tmp_path / "cost.jsonl").write_text("", encoding="utf-8")

    result = CliRunner().invoke(main, ["cost", "--month", "2026/05"])

    assert result.exit_code != 0
    assert "YYYY-MM" in result.output
