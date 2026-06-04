from __future__ import annotations

import json
import os
import re

import keypulse.i18n as i18n
from keypulse.pipeline.daily_summary import (
    _render_text_table_block,
    build_cluster_stubs_from_narrative,
    build_topic_status_snapshot_from_narrative,
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


def test_daily_summary_preserves_cluster_scene_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    write_daily_summary(
        date="2026-05-09",
        clusters=[
            {
                "slug": "keypulse-daily",
                "display_name": "KeyPulse Daily",
                "narrative_one_line": "补现场指纹传递。",
                "event_count": 4,
                "time_range": ["09:00", "10:00"],
                "merge_candidate_with": [],
                "dwell_minutes": 60.0,
                "revisit_count": 2,
                "cross_app_count": 3,
            }
        ],
        misc=[],
        topic_snapshot={},
        cost={"in_tokens": 1, "out_tokens": 2, "cost_usd": 0.0},
    )

    payload = read_daily_summary("2026-05-09")

    assert payload is not None
    assert payload["events"][0]["dwell_minutes"] == 60.0
    assert payload["events"][0]["revisit_count"] == 2
    assert payload["events"][0]["cross_app_count"] == 3
    assert payload["clusters"][0]["dwell_minutes"] == 60.0
    assert payload["clusters"][0]["revisit_count"] == 2
    assert payload["clusters"][0]["cross_app_count"] == 3


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


def test_build_cluster_stubs_from_narrative_h3_slug_collision_keeps_unique():
    # 同前缀+中文后缀的 H3 标题，原 _slugify_narrative_topic 只取 ASCII
    # 词会都坍缩成 "corpusflow"，下游 cluster_id 唯一键退化导致 things
    # 坍缩。这里断言 stub slug 必须互不相同。
    markdown = """
# 2026-05-29

## 今天做的事

### CorpusFlow 专利材料
09:25 讨论"专利去 AI 味"，敲定 repo public/private 时间表。

### CorpusFlow 代码调整
14:10 normalize_generation_frame 改 semantic_status=no_reply_expec。

### 奇趣宝 Demo
16:00 排期剧本对齐。
"""
    clusters = build_cluster_stubs_from_narrative("2026-05-29", markdown)
    slugs = [c["slug"] for c in clusters]
    assert len(slugs) == len(set(slugs)), f"slug collisions: {slugs}"
    assert clusters[0]["slug"] == "corpusflow"
    assert clusters[1]["slug"] == "corpusflow-2"
    assert clusters[0]["display_name"] == "CorpusFlow 专利材料"
    assert clusters[1]["display_name"] == "CorpusFlow 代码调整"


def test_build_topic_status_snapshot_h3_slug_collision_keeps_unique():
    markdown = """
# 2026-05-29

## 今天做的事

### CorpusFlow 专利材料
完成专利去 AI 味第一轮。

### CorpusFlow 代码调整
推进 normalize_generation_frame 重构。
"""
    snapshot = build_topic_status_snapshot_from_narrative("2026-05-29", markdown)
    # 两个 H3 必须各占一条，不能被同 slug 后写覆盖
    assert len(snapshot) == 2
    names = {item["name"] for item in snapshot.values()}
    assert names == {"CorpusFlow 专利材料", "CorpusFlow 代码调整"}
    assert "corpusflow" in snapshot
    assert "corpusflow-2" in snapshot


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


def test_render_daily_markdown_prepends_frontmatter_aliases_and_tags_with_iso_week(monkeypatch):
    monkeypatch.setenv("KEYPULSE_LANG", "zh")
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)
    body = render_daily_markdown(
        date="2026-05-21",
        topics=[],
        events=[],
        topic_snapshot={},
    )
    lines = body.splitlines()
    assert lines[0] == "---"
    assert lines[1] == "aliases:"
    assert '  - "2026-05-21 周四"' in body
    assert "tags:" in body
    assert '  - "daily"' in body
    assert '  - "daily/2026-W21"' in body
    assert "📍 Asia/Shanghai" in body


def test_render_daily_markdown_aliases_switch_to_en_locale(monkeypatch):
    monkeypatch.setenv("KEYPULSE_LANG", "en")
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)
    body = render_daily_markdown(
        date="2026-05-21",
        topics=[],
        events=[],
        topic_snapshot={},
    )
    assert '  - "2026-05-21 Thu"' in body


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
                "display_name": "daily v3 M5 phase2 核心收敛",
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
    # 段名单一权威源：取 cluster.display_name，不取 topic.display（见 heading_single_source 契约测试）
    assert "### [[daily v3 M5 phase2 核心收敛]]" in body
    assert "周报 v3 设计与落地" not in body
    assert "## 今日 raw events (unanchored)" not in body
    assert "## 今日涉及的主题" not in body


def test_render_daily_markdown_h3_matches_cluster_display_name_when_anchor_display_differs():
    """LLM 写 H3 用 cluster.display_name；renderer 需用 events_ref 反查命中，不能只看 anchor.display"""
    llm_md = (
        "## 今日要点\n\n要点正文\n\n"
        "## 今天做的事\n\n"
        "### 周报设计与成功标准定义\n\n"
        "01:08-01:45 你与Claude协作明确周报成功标准，采用Q3/Q2/Q1排列组合。\n"
    )
    body = render_daily_markdown(
        date="2026-05-08",
        topics=[
            {
                "anchor": "weekly-v3-rollout",
                "anchor_state": "continuing",
                "narrative": "（120字截断后的索引片段——不应被采用）",
                "decisions": [],
                "shipped": [],
                "events_ref": ["topic-be3da44cbe"],
                "display": "周报 v3 设计与落地",
            }
        ],
        events=[
            {
                "cluster_id": "topic-be3da44cbe",
                "display_name": "周报设计与成功标准定义",
                "narrative_one_line": "（120字索引）",
                "event_count": 5,
                "time_range": ["01:08", "01:45"],
                "anchored_to": "weekly-v3-rollout",
            }
        ],
        unanchored=[],
        narrative_markdown=llm_md,
    )
    assert "01:08-01:45 你与Claude协作明确周报成功标准" in body
    assert "（120字截断后的索引片段——不应被采用）" not in body


def _h3_headings(body: str) -> list[str]:
    """提取「今天做的事」区块里的 H3 段名（去掉 anchor 链接语法）。"""
    headings: list[str] = []
    in_section = False
    for line in body.splitlines():
        if line.startswith("## "):
            in_section = line.strip() == "## 今天做的事"
            continue
        if in_section and line.startswith("### "):
            text = line[4:].strip()
            # _anchor_link 渲染成 [[anchor|display]] 或 [[anchor]]，取可见 display
            m = re.match(r"\[\[([^\]]*)\]\]", text)
            if m:
                inner = m.group(1)
                text = inner.split("|", 1)[1] if "|" in inner else inner
            headings.append(text.strip())
    return headings


def test_render_daily_markdown_heading_single_source_is_cluster_display_name():
    """契约：段名只能由 clusters[].display_name 产出（确定性、干净）。

    topic.display 由 L0 锚定步 LLM 重写，会带「完成XX」前缀/幻觉——render 必须用 events_ref
    反查 cluster.display_name 作段名，绝不逐字取 topic.display。任何回到 topic.display 即此测试红。
    防 docs/refactor-pipeline-dedup.md 第 1 层「完成XX」回归。
    """
    body = render_daily_markdown(
        date="2026-06-03",
        topics=[
            {
                "anchor": "carmind-intent",
                "anchor_state": "continuing",
                "narrative": "",
                "decisions": [],
                "shipped": [],
                "events_ref": ["c-1"],
                "display": "Carmind-完成落域仲裁意图模块架构设计",  # 脏：LLM 锚定步重写
            },
            {
                "anchor": "",
                "anchor_state": "continuing",
                "narrative": "",
                "decisions": [],
                "shipped": [],
                "events_ref": ["c-2"],
                "display": "KeyPulse-完成周报结构问题修复",  # 脏
            },
        ],
        events=[
            {"cluster_id": "c-1", "display_name": "Carmind 意图判别模块并发架构讨论"},  # 净
            {"cluster_id": "c-2", "display_name": "KeyPulse W22周报整治提交"},  # 净
        ],
        unanchored=[],
        narrative_markdown="## 今天做的事\n\n### Carmind 意图判别模块并发架构讨论\n\n正文一\n\n### KeyPulse W22周报整治提交\n\n正文二\n",
    )
    headings = _h3_headings(body)
    assert headings == ["Carmind 意图判别模块并发架构讨论", "KeyPulse W22周报整治提交"]
    # 脏的 topic.display 绝不能出现在段名里
    assert not any("完成" in h for h in headings)
    assert "Carmind-完成落域仲裁意图模块架构设计" not in body
    assert "KeyPulse-完成周报结构问题修复" not in body


def test_render_daily_markdown_heading_falls_back_to_topic_display_when_no_cluster():
    """护栏：cluster.display_name 缺失（events_ref 查不到）时才兜底用 topic.display，不崩。"""
    body = render_daily_markdown(
        date="2026-06-03",
        topics=[
            {
                "anchor": "",
                "anchor_state": "continuing",
                "narrative": "兜底正文",
                "decisions": [],
                "shipped": [],
                "events_ref": ["missing-ref"],
                "display": "孤儿主题兜底名",
            }
        ],
        events=[],
        unanchored=[],
        narrative_markdown="",
    )
    assert "### 孤儿主题兜底名" in body


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
        "## 今天的卡点",
        "## 明日的锚点",
    ]
    assert "## 今天的事件卡" not in body
    assert "## 跨日延续" not in body
    assert "- [[被阻塞主题]]" in body


def test_render_daily_markdown_appends_algorithm_trace_from_log_and_cost(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    data_dir = tmp_path / ".keypulse"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "log.md").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-05-20T09:10:00Z",
                        "capability": "daily_orchestrator",
                        "date": "2026-05-19",
                        "trigger": "18:00",
                        "decision": "events_capped",
                        "reason": "token_guard",
                        "count": 7,
                        "capped": 6,
                        "event_count": 7,
                        "capped_count": 6,
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-05-20T09:16:00Z",
                        "capability": "daily_orchestrator",
                        "date": "2026-05-19",
                        "trigger": "18:00",
                        "decision": "flagship_repair",
                        "reason": "things_lt_3",
                        "before_things": 2,
                        "repair_things": 2,
                        "final_things": 2,
                        "failure_reason": "repair_things_lt_3:2",
                        "status": "degraded",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-05-20T09:17:00Z",
                        "capability": "daily_orchestrator",
                        "date": "2026-05-19",
                        "trigger": "18:00",
                        "tier": "flagship",
                        "strategy": "flagship",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (data_dir / "cost.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-05-20T09:11:00Z",
                        "capability": "daily_flagship",
                        "model": "cloud/doubao-seed-1-6",
                        "tier": "premium",
                        "in_tokens": 22041,
                        "out_tokens": 2055,
                        "cost_usd": 0.03125,
                        "cache_hit": False,
                        "prompt_version": "daily_flagship.v2",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-05-20T09:12:00Z",
                        "capability": "L0_anchor",
                        "model": "cloud/doubao-seed-1-6",
                        "tier": "premium",
                        "in_tokens": 14591,
                        "out_tokens": 2306,
                        "cost_usd": 0.021,
                        "cache_hit": False,
                        "prompt_version": "L0_anchor.v1",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    raw_rows = [
        {
            "id": 1,
            "ts_start": "2026-05-19T01:55:00+00:00",
            "source": "keyboard_chunk",
            "content_text": "改 self_heal 时区，修复 kickstart 反复 SIGTERM",
        },
        {
            "id": 2,
            "ts_start": "2026-05-19T01:58:00+00:00",
            "source": "keyboard_chunk",
            "content_text": "接 browser_url 到 daily orchestrator",
        },
        {
            "id": 3,
            "ts_start": "2026-05-19T02:01:00+00:00",
            "source": "keyboard_chunk",
            "content_text": "browser 自动发现去 hardcode 白名单",
        },
        {
            "id": 4,
            "ts_start": "2026-05-19T02:04:00+00:00",
            "source": "keyboard_chunk",
            "content_text": "日报底部补算法 Trace",
        },
        {
            "id": 5,
            "ts_start": "2026-05-19T02:07:00+00:00",
            "source": "keyboard_chunk",
            "content_text": "token_guard 砍到 60 条",
        },
        {
            "id": 6,
            "ts_start": "2026-05-19T02:10:00+00:00",
            "source": "keyboard_chunk",
            "content_text": "整理 LLM stage 调用明细",
        },
        {
            "id": 7,
            "ts_start": "2026-05-19T02:13:00+00:00",
            "source": "keyboard_chunk",
            "content_text": "补 sample events",
        },
    ]
    monkeypatch.setattr("keypulse.pipeline.daily_summary.query_raw_events", lambda **_kwargs: raw_rows)

    body = render_daily_markdown(
        date="2026-05-19",
        topics=[
            {
                "anchor": "self-heal-fix",
                "anchor_state": "continuing",
                "narrative": "修 self_heal / browser_url / 自动发现。",
                "decisions": [],
                "shipped": [],
                "events_ref": [],
                "display": "self_heal 修复",
            },
            {
                "anchor": "trace-debug",
                "anchor_state": "continuing",
                "narrative": "补算法 Trace 方便肉眼 debug。",
                "decisions": [],
                "shipped": [],
                "events_ref": [],
                "display": "算法 Trace",
            },
        ],
        events=[],
        unanchored=[],
        event_cards=[
            ("event-01", "事件卡 1"),
            ("event-02", "事件卡 2"),
            ("event-03", "事件卡 3"),
            ("event-04", "事件卡 4"),
            ("event-05", "事件卡 5"),
            ("event-06", "事件卡 6"),
        ],
        topic_snapshot={},
    )

    assert "\n---\n\n## 🔬 算法 Trace（自检用）" in body
    assert "```text" in body
    assert re.search(r"raw events\s+:\s+7", body)
    assert re.search(r"capped\s+:\s+6 \(token_guard\)", body)
    assert re.search(r"聚类策略\s+:\s+flagship 一步法（LLM 直接产 things）", body)
    assert re.search(r"things\s+:\s+2", body)
    assert re.search(r"events 卡片\s+:\s+0", body)
    assert re.search(r"quality_gate\s+:\s+warn", body)
    assert "clusters |" not in body
    assert "走的 path" not in body
    assert "**数据采集源**（当日 raw events 按 source 聚合）" in body
    assert re.search(r"source\s+events\s+最早\s+最晚\s+状态", body)
    assert re.search(r"keyboard_chunk\s+7\s+09:55\s+10:13\s+ok", body)
    assert re.search(r"window\s+0\s+—\s+—\s+⚠ silent", body)
    assert "**repair 自检**" in body
    assert "- 触发原因：things_lt_3" in body
    assert "- H3 计数：2 → 2 → 2" in body
    assert "- 失败原因：repair_things_lt_3:2" in body
    assert re.search(r"stage\s+model\s+in→out tokens\s+状态", body)
    assert re.search(r"daily_flagship\s+doubao-seed-1-6\s+22041→2055\s+ok", body)
    assert re.search(r"L0_anchor\s+doubao-seed-1-6\s+14591→2306\s+ok", body)
    assert "09:55 keyboard_chunk ·" in body
    assert "改 self_heal 时区" in body


def test_render_daily_markdown_includes_cross_day_section_from_narrative(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    body = render_daily_markdown(
        date="2026-05-09",
        topics=[],
        events=[],
        topic_snapshot={},
        narrative_markdown="## 跨日延续\n\n昨天的主线今天继续推进。\n\n## 明日的锚点\n\n占位",
    )

    assert "## 跨日延续\n\n昨天的主线今天继续推进。" in body


def test_trace_text_table_pads_silent_dash_cells_to_column_max_width():
    block = _render_text_table_block(
        headers=["source", "events", "最早", "最晚", "状态"],
        rows=[
            ["spotlight", "4", "11:35", "20:38", "ok"],
            ["ax_text", "2", "17:59", "19:21", "ok"],
            ["manual", "0", "—", "—", "⚠ silent"],
            ["ocr", "0", "—", "—", "⚠ silent"],
        ],
        align_right_cols={1},
    )

    assert "manual          0  —      —      ⚠ silent" in block
    assert "ocr             0  —      —      ⚠ silent" in block


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
        narrative_markdown="",
    )

    assert "## 跨日延续" not in body
    assert "## 今天的卡点" not in body

