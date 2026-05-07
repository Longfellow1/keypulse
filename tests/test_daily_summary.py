from __future__ import annotations

from pathlib import Path

from keypulse.pipeline.daily_summary import read_daily_summary, write_daily_summary


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
    assert set(payload.keys()) == {"date", "clusters", "misc_event_ids", "topic_status_snapshot", "cost"}
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
