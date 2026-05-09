from __future__ import annotations

from pathlib import Path

from keypulse.pipeline.daily_summary import (
    build_cluster_stubs_from_narrative,
    build_topic_status_snapshot_from_narrative,
    merge_topic_status_snapshots,
    read_daily_summary,
    write_daily_summary,
)


def test_write_then_read_daily_summary(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    path = write_daily_summary(
        date="2026-05-06",
        clusters=[
            {
                "slug": "keypulse-hud-fix",
                "display_name": "修 HUD 信号源",
                "narrative_one_line": "完成 HUD 成本栏与跳转修复。",
                "event_count": 5,
                "time_range": ["00:05", "01:32"],
                "merge_candidate_with": [],
            }
        ],
        misc=["evt-1"],
        topic_snapshot={"keypulse-hud-fix": "active"},
        cost={"in_tokens": 4700, "out_tokens": 1500, "cost_usd": 0.0035},
    )

    assert path == tmp_path / ".keypulse" / "daily-summary" / "2026-05-06.json"
    loaded = read_daily_summary("2026-05-06")
    assert loaded is not None
    assert loaded["date"] == "2026-05-06"
    assert loaded["clusters"][0]["slug"] == "keypulse-hud-fix"
    assert loaded["misc_event_ids"] == ["evt-1"]
    assert loaded["topic_status_snapshot"] == {"keypulse-hud-fix": "active"}
    assert loaded["cost"] == {"in_tokens": 4700, "out_tokens": 1500, "cost_usd": 0.0035}


def test_read_daily_summary_returns_none_for_missing_date(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    assert read_daily_summary("2026-05-07") is None


def test_daily_summary_has_complete_schema_and_no_tmp_residue(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    write_daily_summary(
        date="2026-05-08",
        clusters=[
            {
                "slug": "topic-a",
                "display_name": "主题 A",
                "narrative_one_line": "叙事",
                "event_count": 2,
                "time_range": ["09:00", "09:30"],
                "merge_candidate_with": ["topic-b"],
            }
        ],
        misc=[],
        topic_snapshot={},
        cost={"in_tokens": 1, "out_tokens": 2, "cost_usd": 0.0},
    )

    payload = read_daily_summary("2026-05-08")
    assert payload is not None
    assert {"date", "clusters", "misc_event_ids", "topic_status_snapshot", "cost"}.issubset(set(payload.keys()))
    assert set(payload["clusters"][0].keys()) == {
        "slug",
        "display_name",
        "narrative_one_line",
        "event_count",
        "time_range",
        "merge_candidate_with",
    }
    assert set(payload["cost"].keys()) == {"in_tokens", "out_tokens", "cost_usd"}

    summary_dir = tmp_path / ".keypulse" / "daily-summary"
    assert list(summary_dir.glob("*.tmp")) == []
    assert list(summary_dir.glob("*.json.tmp")) == []


def test_topic_status_snapshot_from_things_narrative_happy_path():
    markdown = """
# 2026-05-08

## 今天做的事

### KeyPulse Weekly Fallback
你修复了 LLM 失败后的降级路径，并通过测试确认输出不为空。

### QueryPlan 生成器
你首次记录 QueryPlan 生成器的输入输出。

## 明天的锚点
"""

    snapshot = build_topic_status_snapshot_from_narrative("2026-05-08", markdown)

    assert snapshot["keypulse-weekly-fallback"]["name"] == "KeyPulse Weekly Fallback"
    assert snapshot["keypulse-weekly-fallback"]["state"] == "completed"
    assert snapshot["keypulse-weekly-fallback"]["last_seen_date"] == "2026-05-08"
    assert snapshot["keypulse-weekly-fallback"]["evidence_dates"] == ["2026-05-08"]
    query_slug = next(slug for slug, item in snapshot.items() if item["name"] == "QueryPlan 生成器")
    assert snapshot[query_slug]["state"] == "started"


def test_topic_status_snapshot_empty_narrative_returns_empty():
    markdown = """
# 2026-05-08

## 今日要点
没有 H3 things narrative。

## 明天的锚点
"""

    assert build_topic_status_snapshot_from_narrative("2026-05-08", markdown) == {}


def test_build_cluster_stubs_from_narrative_h3_sections():
    markdown = """
# 2026-05-08

## 今天做的事

### KeyPulse Weekly Fallback
00:05 你修复了 LLM 失败后的降级路径，并通过测试确认输出不为空。

### QueryPlan 生成器
09:20 你首次记录 QueryPlan 生成器的输入输出。
"""
    clusters = build_cluster_stubs_from_narrative("2026-05-08", markdown)
    assert clusters
    assert clusters[0]["slug"] == "keypulse-weekly-fallback"
    assert clusters[0]["time_range"] == ["00:05", "00:05"]
    assert clusters[1]["display_name"] == "QueryPlan 生成器"
    assert clusters[1]["event_count"] >= 1


def test_merge_topic_status_snapshot_single_topic_across_days():
    day1 = {
        "keypulse-weekly": {
            "name": "KeyPulse Weekly",
            "state": "started",
            "last_seen_date": "2026-05-07",
            "evidence_dates": ["2026-05-07"],
        }
    }
    day2 = {
        "keypulse-weekly": {
            "name": "KeyPulse Weekly",
            "state": "completed",
            "last_seen_date": "2026-05-08",
            "evidence_dates": ["2026-05-08"],
        }
    }

    merged = merge_topic_status_snapshots([day1, day2])

    assert merged["keypulse-weekly"]["state"] == "completed"
    assert merged["keypulse-weekly"]["last_seen_date"] == "2026-05-08"
    assert merged["keypulse-weekly"]["evidence_dates"] == ["2026-05-07", "2026-05-08"]
