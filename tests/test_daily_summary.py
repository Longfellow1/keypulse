from __future__ import annotations

import os
from pathlib import Path

from keypulse.pipeline.daily_summary import (
    build_cluster_stubs_from_narrative,
    build_topic_status_snapshot_from_narrative,
    filter_daily_event_cards,
    merge_topic_status_snapshots,
    read_daily_summary,
    render_daily_markdown,
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


def test_render_daily_markdown_dual_layer_prefers_topics_and_unanchored_desc_time():
    body = render_daily_markdown(
        date="2026-05-09",
        topics=[
            {
                "anchor": "weekly-v3-rollout",
                "anchor_state": "continuing",
                "narrative": "你完成了 daily v3 M5 的 phase2 核心收敛。",
                "decisions": [],
                "shipped": [],
                "events_ref": ["c1"],
                "display": "周报 v3 设计与落地",
            }
        ],
        events=[
            {
                "cluster_id": "c1",
                "display_name": "daily-v3-m5-phase2",
                "narrative_one_line": "接通 anchor + 删除错误升格机制",
                "event_count": 4,
                "time_range": ["18:00", "20:00"],
                "anchored_to": "weekly-v3-rollout",
            }
        ],
        unanchored=[
            {
                "cluster_id": "u1",
                "display_name": "提醒邮件",
                "narrative_one_line": "浏览了通知邮件",
                "event_count": 1,
                "time_range": ["08:30", "08:31"],
                "anchored_to": None,
            },
            {
                "cluster_id": "u2",
                "display_name": "登录页",
                "narrative_one_line": "登录某服务后台",
                "event_count": 1,
                "time_range": ["12:30", "12:31"],
                "anchored_to": None,
            },
        ],
    )
    assert "## 今日要点" in body
    assert "## 今天做的事" in body
    assert "### [[weekly-v3-rollout|周报 v3 设计与落地]]" in body
    assert "## 今日 raw events (unanchored)" not in body
    assert "## 今日涉及的主题" not in body


def test_render_daily_markdown_phase_a_section_contract_and_event_cards(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    event_dir = tmp_path / ".keypulse" / "events" / "2026-05-09"
    event_dir.mkdir(parents=True)
    older = event_dir / "0900-with-title.md"
    newer = event_dir / "1000-without-title.md"
    older.write_text("# 有标题事件\n\n正文", encoding="utf-8")
    newer.write_text("没有 H1 的正文", encoding="utf-8")
    older_mtime = 1_700_000_000
    newer_mtime = 1_700_000_100
    older.touch()
    newer.touch()

    os.utime(older, (older_mtime, older_mtime))
    os.utime(newer, (newer_mtime, newer_mtime))

    body = render_daily_markdown(
        date="2026-05-09",
        topics=[
            {
                "anchor": "weekly-v3-rollout",
                "anchor_state": "continuing",
                "narrative": "5/9 继续推进周报 v3，完成 daily renderer phase A 契约验证与事件卡回填。",
                "decisions": ["确定 daily renderer 只保留阶段 A 段名契约"],
                "shipped": ["落地事件卡 wikilink"],
                "events_ref": [],
                "display": "周报 v3 设计与落地",
            },
            {
                "anchor": "blocked-topic",
                "anchor_state": "blocked",
                "narrative": "某条链路仍然失败，需要明天继续排查。",
                "decisions": [],
                "shipped": [],
                "events_ref": [],
                "display": "被阻塞主题",
            },
        ],
        topic_snapshot={
            "weekly-v3-rollout": {
                "name": "周报 v3 设计与落地",
                "state": "in_progress",
                "last_seen_date": "2026-05-09",
                "evidence_dates": ["2026-05-08", "2026-05-09"],
            }
        },
    )

    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 今日要点",
        "## 今天做的事",
        "## 今天的事件卡",
        "## 跨日延续",
        "## 今天的卡点",
        "## 明日的锚点",
    ]
    assert body.index("[[../.keypulse/events/2026-05-09/1000-without-title|1000-without-title]]") < body.index(
        "[[../.keypulse/events/2026-05-09/0900-with-title|有标题事件]]"
    )
    assert "## 跨日延续\n\n## 今天的卡点" in body
    assert "- [[blocked-topic|被阻塞主题]]" in body


def test_render_daily_markdown_omits_event_cards_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    body = render_daily_markdown(
        date="2026-05-09",
        topics=[],
        events=[],
        topic_snapshot={},
    )

    assert "## 今天的事件卡" not in body


def test_render_daily_markdown_omits_blocked_section_when_no_blocked_topics(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    body = render_daily_markdown(
        date="2026-05-09",
        topics=[
            {
                "anchor": "weekly-v3-rollout",
                "anchor_state": "continuing",
                "narrative": "5/9 继续推进周报 v3，完成 daily renderer phase A 契约验证与事件卡回填。",
                "decisions": [],
                "shipped": [],
                "events_ref": [],
                "display": "周报 v3 设计与落地",
            }
        ],
        events=[],
        topic_snapshot={},
    )

    assert "## 跨日延续" in body
    assert "## 今天的卡点" not in body


def test_filter_daily_event_cards_uses_rule_scores_and_never_calls_gateway():
    class _Gateway:
        def call(self, capability, prompt, **kwargs):
            raise AssertionError("event card filtering must not call LLM")

    cards = [
        ("export-https-proxy-http-127-7890", "export https_proxy=http://127.0.0.1:7890"),
        ("https-github-com-example-project", "https://github.com/example/project"),
        ("1948-obsidian-clipboard-copy-query", "obsidian clipboard copy query"),
        ("1142-cd-users-harland-go-corpusflow-npm", "cd /Users/Harland/Go/CorpusFlow && npm run keepalive"),
        ("1521-weekly-v3-review", "周报设计方案迭代与评审"),
        ("1406-lion-carmind-agent-runtime-项目-brief", "Lion-CarMind / Agent Runtime 项目 Brief"),
        ("1957-car-agent-product", "座舱实时语音 Agent 产品化"),
        ("1023-deep-fix-v5-skill", "安装并启动 deep-fix-v5 技能"),
        ("1445-walking-or-driving", "结论：要看你洗的对象是谁"),
        ("1924-http-127-7860", "http://127.0.0.1:7860"),
    ]

    selected = filter_daily_event_cards(cards, model_gateway=_Gateway(), target_count=6)

    assert len(selected) == 6
    selected_slugs = [slug for slug, _title in selected]
    assert "1521-weekly-v3-review" in selected_slugs
    assert "1957-car-agent-product" in selected_slugs
    assert "export-https-proxy-http-127-7890" not in selected_slugs
    assert "https-github-com-example-project" not in selected_slugs


def test_filter_daily_event_cards_defaults_to_six_representative_items():
    cards = [(f"event-{index:02d}", f"代表性事件 {index:02d}") for index in range(20)]

    selected = filter_daily_event_cards(cards)

    assert len(selected) == 6
