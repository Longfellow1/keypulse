from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from click.testing import CliRunner

from keypulse.cli import main
from keypulse.pipeline.daily_orchestrator import DailyOrchestratorError, run_daily
from keypulse.pipeline.daily_orchestrator import (
    _cap_flagship_events_for_prompt,
    _component_time_range,
    _event_value_density,
    _extract_event_payload,
    _fallback_summary_clusters_from_events,
    maybe_run_daily_for_sync,
    run_daily_after_obsidian_sync,
)
from keypulse.pipeline.event_intake import is_input_fragment
from keypulse.pipeline.model import ModelBackend
from keypulse.pipeline.triggers import record_trigger
from keypulse.store.db import close, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


DAILY_MARKDOWN = """📍 Asia/Shanghai

# 2026-05-01

## 今日要点

你今天确认 daily 生成策略需要按模型能力分流，旗舰模型承担整篇生成，预算模型保留结构化聚类交接。这让日报质量和调用成本可以被同一个入口稳定管理。

## 今天做的事

### KeyPulse Daily Strategy

你围绕 KeyPulse daily 管线完成策略拆分，把事件抽取、主题命名和日报写入放回编排层。这个调整让大模型走单次生成，小模型继续依赖聚类结果，边界更清楚。

## 明日的锚点

> 明天我想：______
>
> _写一句话留给明天的自己_
"""

FLAGSHIP_OK_MARKDOWN = """📍 Asia/Shanghai

# 2026-05-01

## 今日要点

你今天把 daily 生成链路收敛到旗舰单次生成主线，并补上了 things 数不足时的修复兜底，保证输出质量门槛不会被漏过。

## 今天做的事

### KeyPulse Daily Strategy

你确认旗舰路径继续保留，不回退到 budget 或 cluster 分支，并把修复逻辑限定在同一 capability 内完成。

### Daily Orchestrator Repair

你在旗舰首轮输出后增加 H3 数检查，遇到 `things<3` 时触发 `REPAIR MODE` 二次重写，避免日报主体只剩 1-2 个主题段。

### Prompt Hard Constraints

你把 prompt 明确改成 `components_count >= 3` 时必须输出至少 3 个 H3，并要求看到 `REPAIR MODE` 必须整篇重写。

## 明日的锚点

> 明天我想：______
>
> _写一句话留给明天的自己_
"""

FLAGSHIP_REPAIR_SOURCE_MARKDOWN = """📍 Asia/Shanghai

# 2026-05-01

## 今日要点

你今天聚焦在 daily 编排和提示词修复两条线，先确认了根因，再准备补 repair 兜底。

## 今天做的事

### Daily 编排

你调整了旗舰编排主路径，保证不回退到 budget。

### 提示词修复

你把输出结构约束提炼成明确规则。

## 明日的锚点

> 明天我想：______
>
> _写一句话留给明天的自己_
"""


class FakeGateway:
    def __init__(self, model: str, responses: dict[str, Any]):
        self.backend = ModelBackend(kind="openai_compatible", base_url="https://example.test/v1", model=model)
        self.responses = responses
        self.calls: list[str] = []
        self.inputs: list[dict[str, Any]] = []

    def select_backend(self, stage: str = "write") -> ModelBackend:
        return self.backend

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs) -> Any:
        self.calls.append(capability)
        self.inputs.append({"capability": capability, "input_data": input_data, "prompt": prompt})
        response = self.responses[capability]
        if isinstance(response, list):
            if not response:
                raise AssertionError(f"no mock response left for capability={capability}")
            response = response.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _write_config(tmp_path: Path, *, cloud_model: str, cloud_tier: str = "", active_profile: str = "cloud-only") -> None:
    config_dir = tmp_path / ".keypulse"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath("config.toml").write_text(
        f"""
[model]
active_profile = "{active_profile}"

[model.cloud]
kind = "openai_compatible"
base_url = "https://example.test/v1"
model = "{cloud_model}"
tier = "{cloud_tier}"

[model.local]
kind = "lm_studio"
base_url = "http://127.0.0.1:1234"
model = "qwen2.5-7b"
tier = ""
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "source": "ax_text",
            "speaker": "user",
            "ts_start": "2026-05-01T01:00:00+00:00",
            "ts_end": "2026-05-01T01:01:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "process_name": "Codex Helper",
            "content_text": "KeyPulse daily strategy refactor model tier routing",
            "content_hash": "hash-1",
            "metadata_json": json.dumps({"entities": {"session_id": "s1", "named_entities": ["KeyPulse"]}}),
            "session_id": "s1",
            "semantic_weight": 0.8,
            "user_present": 1,
        },
        {
            "id": 2,
            "source": "ax_text",
            "speaker": "ai",
            "ts_start": "2026-05-01T01:03:00+00:00",
            "ts_end": "2026-05-01T01:04:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "process_name": "Codex Helper",
            "content_text": "Budget path should call L1 then one L2 narrative",
            "content_hash": "hash-2",
            "metadata_json": json.dumps({"entities": {"session_id": "s1", "named_entities": ["KeyPulse"]}}),
            "session_id": "s1",
            "semantic_weight": 0.7,
            "user_present": 1,
        },
        {
            "id": 3,
            "source": "clipboard",
            "speaker": "user",
            "ts_start": "2026-05-01T03:00:00+00:00",
            "ts_end": None,
            "app_name": "Chrome",
            "window_title": "Misc",
            "process_name": "Google Chrome",
            "content_text": "misc unrelated browsing",
            "content_hash": "hash-3",
            "metadata_json": json.dumps({"entities": {"session_id": "s2"}}),
            "session_id": "s2",
            "semantic_weight": 0.3,
            "user_present": 1,
        },
    ]


def _patch_io(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_topic_anchor._DEFAULT_PATH",
        tmp_path / ".keypulse" / "weekly-anchor.json",
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_rows_for_date", lambda _date: rows)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._filter_for_trigger", lambda _date, _trigger, loaded: loaded)
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )


def test_flagship_path_calls_one_llm_and_skips_topics(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="doubao-seed-1-6-250615")
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway(
        "doubao-seed-1-6-250615",
        {
            "daily_flagship": {"markdown": FLAGSHIP_OK_MARKDOWN},
            "L0_anchor": {"assignments": {"keypulse-daily-strategy": "weekly-v3-rollout"}, "new_anchors": []},
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    summary = run_daily("2026-05-01", trigger="18:00")

    assert gateway.calls.count("daily_flagship") in {1, 2}
    assert gateway.calls.count("L0_anchor") in {1, 2}
    assert summary.cluster_count == 0
    assert summary.misc_event_ids == ()
    daily_body = Path(summary.daily_path).read_text(encoding="utf-8")
    assert "## 今天做的事" in daily_body
    assert "## 今日 raw events (unanchored)" not in daily_body
    summary_payload = json.loads(Path(summary.summary_path).read_text(encoding="utf-8"))
    assert len(summary_payload["events"]) >= 1
    assert "topics" in summary_payload
    assert (tmp_path / ".keypulse" / "weekly-anchor.json").exists()
    assert not (tmp_path / ".keypulse" / "hot.md").exists()
    flagship_input = next(item["input_data"] for item in gateway.inputs if item["capability"] == "daily_flagship")
    assert "clusters" in flagship_input
    assert flagship_input["clusters"][0]["dwell_minutes"] == 3.0
    assert flagship_input["clusters"][0]["cross_app_count"] == 1


def test_budget_path_calls_l1_l2_once_and_l3_for_new_topic(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="qwen2.5-7b")
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway(
        "qwen2.5-7b",
        {
            "L1_cluster_review": {
                "clusters": [
                    {"component_id": "c1", "topic_action": "new", "reason": "new work"},
                    {"component_id": "c2", "topic_action": "misc", "reason": "noise"},
                ],
                "misc_event_ids": ["3"],
            },
            "L2_narrative": {"markdown": DAILY_MARKDOWN},
            "L3_topic_naming": {
                "slug": "keypulse-daily-strategy",
                "display_name": "KeyPulse Daily Strategy",
                "keywords": ["keypulse", "daily", "strategy", "tier", "budget"],
            },
            "L0_anchor": {
                "assignments": {"c1": "weekly-v3-rollout"},
                "new_anchors": [],
            },
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    summary = run_daily("2026-05-01", trigger="18:00")

    assert gateway.calls == ["L1_cluster_review", "L2_narrative", "L3_topic_naming", "L0_anchor"]
    assert summary.cluster_count == 1
    assert summary.misc_event_ids == ("3",)
    assert summary.topic_diffs == ("keypulse-daily-strategy:created",)
    assert (tmp_path / ".keypulse" / "hot.md").read_text(encoding="utf-8").count("keypulse-daily-strategy") == 1
    l2_input = next(item["input_data"] for item in gateway.inputs if item["capability"] == "L2_narrative")
    assert len(l2_input["clusters"]) == 1
    assert len(l2_input["misc_events"]) == 1
    l1_input = next(item["input_data"] for item in gateway.inputs if item["capability"] == "L1_cluster_review")
    assert l1_input["components"][0]["time_range"] == ["09:00", "09:03"]
    assert l1_input["components"][0]["dwell_minutes"] == 3.0
    assert l1_input["components"][0]["revisit_count"] == 0
    assert l1_input["components"][0]["cross_app_count"] == 1
    l3_input = next(item["input_data"] for item in gateway.inputs if item["capability"] == "L3_topic_naming")
    assert [event["timestamp"] for event in l3_input["events"]] == ["05-01 09:00", "05-01 09:03"]
    summary_payload = json.loads(Path(summary.summary_path).read_text(encoding="utf-8"))
    assert summary_payload["events"][0]["dwell_minutes"] == 3.0
    assert summary_payload["events"][0]["revisit_count"] == 0
    assert summary_payload["events"][0]["cross_app_count"] == 1


def test_extract_event_payload_preserves_raw_event_scene_columns():
    payload = _extract_event_payload(
        {
            "id": 10,
            "source": "ax_text",
            "event_type": "window_heartbeat",
            "speaker": "user",
            "ts_start": "2026-05-01T01:00:00+00:00",
            "ts_end": "2026-05-01T01:05:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "process_name": "Codex Helper",
            "content_text": "daily pipeline",
            "content_hash": "abc",
            "metadata_json": json.dumps({"entities": {"session_id": "metadata-sid"}}),
            "sensitivity_level": 0,
            "skipped_reason": None,
            "session_id": "top-sid",
            "semantic_weight": 0.9,
            "user_present": 1,
            "created_at": "2026-05-01T01:00:01+00:00",
        }
    )

    assert payload["id"] == "10"
    assert payload["session_id"] == "top-sid"
    assert payload["window_title"] == "KeyPulse"
    assert payload["process_name"] == "Codex Helper"
    assert payload["ts_end"] == "2026-05-01T01:05:00+00:00"
    assert payload["content_hash"] == "abc"
    assert payload["semantic_weight"] == 0.9
    assert payload["user_present"] == 1
    assert json.loads(payload["metadata_json"])["entities"]["session_id"] == "metadata-sid"


def test_daily_orchestrator_llm_time_exports_use_local_timezone(monkeypatch):
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))
    events = [
        {"id": "1", "ts_start": "2026-05-12T03:11:00+00:00"},
        {"id": "2", "ts_start": "2026-05-12T03:16:00+00:00"},
    ]

    assert _component_time_range(events) == ("11:11", "11:16")

    low_volume = _fallback_summary_clusters_from_events("2026-05-12", events)
    assert low_volume[0]["time_range"] == ["11:11", "11:16"]


def test_maybe_run_daily_for_sync_skips_when_t1_gate_blocks(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "keypulse.pipeline.triggers.should_trigger",
        lambda *args, **kwargs: (False, "T1:no_activity_5h"),
    )
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.run_daily",
        lambda date_str, trigger="18:00": calls.append((date_str, trigger)),
    )

    assert maybe_run_daily_for_sync(
        "2026-05-13",
        db_path=tmp_path / "keypulse.db",
        now=datetime(2026, 5, 13, 4, 0, 0),
    ) == (False, "skip:no_activity")
    assert calls == []


def test_maybe_run_daily_for_sync_skips_recent_success_dedupe(tmp_path, monkeypatch):
    db_path = tmp_path / "keypulse.db"
    now = datetime(2026, 5, 13, 4, 10, 0)
    record_trigger(
        "T2",
        now=datetime(2026, 5, 13, 4, 0, 0),
        db_path=db_path,
        outcome="ran:ok",
        note="daily_orchestrator:2026-05-13",
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "keypulse.pipeline.triggers.should_trigger",
        lambda *args, **kwargs: (True, "T1:activity_ok"),
    )
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.run_daily",
        lambda date_str, trigger="18:00": calls.append((date_str, trigger)),
    )

    assert maybe_run_daily_for_sync("2026-05-13", db_path, now=now) == (False, "skip:dedupe_15min")
    assert calls == []


def test_maybe_run_daily_for_sync_runs_daily_when_gates_allow(tmp_path, monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "keypulse.pipeline.triggers.should_trigger",
        lambda *args, **kwargs: (True, "T1:activity_ok"),
    )
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.run_daily",
        lambda date_str, trigger="18:00": calls.append((date_str, trigger)),
    )

    ran, reason = maybe_run_daily_for_sync(
        "2026-05-13",
        db_path=tmp_path / "keypulse.db",
        now=datetime(2026, 5, 13, 4, 0, 0),
    )

    assert ran is True
    assert reason == "ran:ok"
    assert calls == [("2026-05-13", "18:00")]


def test_maybe_run_daily_for_sync_returns_error_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "keypulse.pipeline.triggers.should_trigger",
        lambda *args, **kwargs: (True, "T1:activity_ok"),
    )
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.run_daily",
        lambda date_str, trigger="18:00": (_ for _ in ()).throw(RuntimeError("llm down")),
    )

    ran, reason = maybe_run_daily_for_sync(
        "2026-05-13",
        db_path=tmp_path / "keypulse.db",
        now=datetime(2026, 5, 13, 4, 0, 0),
    )

    assert ran is False
    assert reason.startswith("error:RuntimeError:")
    assert "llm down" in reason


def test_run_daily_after_obsidian_sync_wraps_maybe_helper(tmp_path, monkeypatch):
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.maybe_run_daily_for_sync",
        lambda date_str, db_path, now=None: calls.append((date_str, db_path)) or (True, "ran:ok"),
    )

    assert run_daily_after_obsidian_sync(
        "2026-05-13",
        db_path=tmp_path / "keypulse.db",
    ) is True
    assert calls == [("2026-05-13", tmp_path / "keypulse.db")]


def test_event_value_density_promotes_user_decisions_over_tool_echo():
    decision_event = {
        "id": "decision",
        "source": "clipboard",
        "speaker": "user",
        "content_text": "我建议选择方案 B，根因是入口职责需要拆开，应该把两个用户路径分开。",
    }
    tool_echo_event = {
        "id": "echo",
        "source": "zsh_history",
        "speaker": "system",
        "content_text": "When using Powerlevel10k with instant prompt, console output during zsh initialization may indicate issues. " * 20,
    }

    assert _event_value_density(decision_event) > _event_value_density(tool_echo_event)


def test_input_fragment_filter_distinguishes_raw_pinyin_from_real_text_and_urls():
    assert is_input_fragment("vpinggzhepiafenAgentcehuaan,") is True
    assert is_input_fragment("https://x.com/feed") is False
    assert is_input_fragment("Agent系统") is False


def test_tier_auto_recognizes_flagship_model(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="deepseek-chat")
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway(
        "deepseek-chat",
        {
            "daily_flagship": {"markdown": FLAGSHIP_OK_MARKDOWN},
            "L0_anchor": {"assignments": {"keypulse-daily-strategy": "weekly-v3-rollout"}, "new_anchors": []},
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    run_daily("2026-05-01", trigger="18:00")

    assert gateway.calls.count("daily_flagship") in {1, 2}
    assert gateway.calls.count("L0_anchor") in {1, 2}


def test_flagship_path_caps_events_to_sixty_and_keeps_high_value_user_signal(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="deepseek-chat")
    rows: list[dict[str, Any]] = []
    for index in range(104):
        rows.append(
            {
                "id": index + 1,
                "source": "ax_text",
                "speaker": "system",
                "ts_start": f"2026-05-01T{index % 24:02d}:00:00+00:00",
                "app_name": "Terminal",
                "window_title": "export HTTPS_PROXY",
                "content_text": "export https_proxy=http://127.0.0.1:7890 " * 8,
                "metadata_json": json.dumps({"entities": {"session_id": f"noise-{index}"}}),
            }
        )
    rows.append(
        {
            "id": 105,
            "source": "clipboard",
            "speaker": "user",
            "ts_start": "2026-05-01T23:55:00+00:00",
            "app_name": "Obsidian",
            "window_title": "Daily decision",
            "content_text": "用户拍板：今天选择规则化 events filter，根因是事件卡价值低，不再调用 LLM。",
            "metadata_json": json.dumps({"entities": {"session_id": "decision"}}),
        }
    )
    _patch_io(monkeypatch, tmp_path, rows)
    gateway = FakeGateway(
        "deepseek-chat",
        {
            "daily_flagship": {"markdown": FLAGSHIP_OK_MARKDOWN},
            "L0_anchor": {"assignments": {"keypulse-daily-strategy": "weekly-v3-rollout"}, "new_anchors": []},
        },
    )
    log_records: list[dict[str, Any]] = []
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._append_log", log_records.append)

    run_daily("2026-05-01", trigger="18:00")

    flagship_input = next(item["input_data"] for item in gateway.inputs if item["capability"] == "daily_flagship")
    compact_events = flagship_input["events"]
    assert len(compact_events) <= 60
    assert any("用户拍板" in item["c"] for item in compact_events)
    assert not any("export https_proxy" in item["c"] for item in compact_events)
    assert any(
        record.get("decision") == "events_capped"
        and record.get("event_count") == 105
        and record.get("capped_count") == len(compact_events)
        and record.get("reason") == "token_guard"
        for record in log_records
    )


def test_flagship_repair_retries_when_things_below_three_and_keeps_quality_gate_ok(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="deepseek-chat")
    rows = _rows()
    _patch_io(monkeypatch, tmp_path, rows)
    monkeypatch.setattr("keypulse.pipeline.daily_summary.query_raw_events", lambda **_kwargs: rows)
    gateway = FakeGateway(
        "deepseek-chat",
        {
            "daily_flagship": [
                {"markdown": FLAGSHIP_REPAIR_SOURCE_MARKDOWN},
                {"markdown": FLAGSHIP_OK_MARKDOWN},
            ],
            "L0_anchor": {"assignments": {"keypulse-daily-strategy": "weekly-v3-rollout"}, "new_anchors": []},
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator._run_anchor_for_clusters",
        lambda **kwargs: (
            [
                {
                    "anchor": str(cluster.get("cluster_id") or ""),
                    "anchor_state": "continuing",
                    "narrative": str(cluster.get("narrative_one_line") or ""),
                    "decisions": [],
                    "shipped": [],
                    "events_ref": [str(cluster.get("cluster_id") or "")],
                    "display": str(cluster.get("display_name") or ""),
                }
                for cluster in kwargs.get("today_clusters", [])
            ],
            [
                {
                    "cluster_id": str(cluster.get("cluster_id") or ""),
                    "display_name": str(cluster.get("display_name") or ""),
                    "narrative_one_line": str(cluster.get("narrative_one_line") or ""),
                    "event_count": int(cluster.get("event_count") or 0),
                    "time_range": list(cluster.get("time_range") or ["00:00", "23:59"]),
                    "anchored_to": str(cluster.get("cluster_id") or "") or None,
                }
                for cluster in kwargs.get("today_clusters", [])
            ],
            [],
        ),
    )

    summary = run_daily("2026-05-01", trigger="18:00")

    assert gateway.calls == ["daily_flagship", "daily_flagship"]
    daily_body = Path(summary.daily_path).read_text(encoding="utf-8")
    assert "| quality_gate | ok |" in daily_body
    assert "**repair 自检**" in daily_body
    assert "- 触发原因：things_lt_3" in daily_body
    assert "- H3 计数：2 → 3 → 3" in daily_body
    assert "- 失败原因：—" in daily_body


def test_flagship_repair_triggers_when_topics_drop_below_three_even_if_narrative_has_three(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="deepseek-chat")
    rows = _rows()
    _patch_io(monkeypatch, tmp_path, rows)
    monkeypatch.setattr("keypulse.pipeline.daily_summary.query_raw_events", lambda **_kwargs: rows)
    gateway = FakeGateway(
        "deepseek-chat",
        {
            "daily_flagship": [
                {"markdown": FLAGSHIP_OK_MARKDOWN},
                {"markdown": FLAGSHIP_OK_MARKDOWN},
            ],
            "L0_anchor": {"assignments": {"keypulse-daily-strategy": "weekly-v3-rollout"}, "new_anchors": []},
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    anchor_state = {"calls": 0}

    def _fake_anchor(**kwargs):
        anchor_state["calls"] += 1
        today_clusters = list(kwargs.get("today_clusters", []))
        selected = today_clusters[:2] if anchor_state["calls"] == 1 else today_clusters
        return (
            [
                {
                    "anchor": str(cluster.get("cluster_id") or ""),
                    "anchor_state": "continuing",
                    "narrative": str(cluster.get("narrative_one_line") or ""),
                    "decisions": [],
                    "shipped": [],
                    "events_ref": [str(cluster.get("cluster_id") or "")],
                    "display": str(cluster.get("display_name") or ""),
                }
                for cluster in selected
            ],
            [
                {
                    "cluster_id": str(cluster.get("cluster_id") or ""),
                    "display_name": str(cluster.get("display_name") or ""),
                    "narrative_one_line": str(cluster.get("narrative_one_line") or ""),
                    "event_count": int(cluster.get("event_count") or 0),
                    "time_range": list(cluster.get("time_range") or ["00:00", "23:59"]),
                    "anchored_to": str(cluster.get("cluster_id") or "") or None,
                }
                for cluster in selected
            ],
            [],
        )

    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._run_anchor_for_clusters", _fake_anchor)

    summary = run_daily("2026-05-01", trigger="18:00")

    assert summary.cluster_count == 0
    assert gateway.calls == ["daily_flagship", "daily_flagship"]
    daily_body = Path(summary.daily_path).read_text(encoding="utf-8")
    assert "| quality_gate | ok |" in daily_body
    assert "- H3 计数：2 → 3 → 3" in daily_body

def test_flagship_event_cap_keeps_hourly_coverage_before_score_fill():
    rows: list[dict[str, Any]] = []
    for index in range(5):
        rows.append(
            {
                "id": str(index + 1),
                "source": "clipboard",
                "speaker": "user",
                "ts_start": f"2026-05-01T01:{index:02d}:00+00:00",
                "app_name": "Obsidian",
                "window_title": "early dense work",
                "content_text": "用户拍板：早上高价值决策 " * 8,
                "metadata_json": "{}",
            }
        )
    rows.append(
        {
            "id": "99",
            "source": "manual",
            "speaker": "user",
            "ts_start": "2026-05-01T22:30:00+00:00",
            "app_name": "Terminal",
            "window_title": "late verification",
            "content_text": "晚上完成真实数据重渲染验收。",
            "metadata_json": "{}",
        }
    )

    selected, capped = _cap_flagship_events_for_prompt(rows, limit=3)

    assert capped is True
    assert len(selected) <= 3
    assert any(str(event["ts_start"]).startswith("2026-05-01T01:") for event in selected)
    assert any(str(event["ts_start"]).startswith("2026-05-01T22:") for event in selected)


def test_tier_override_wins_over_model_card(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="deepseek-chat", cloud_tier="budget")
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway(
        "deepseek-chat",
        {
            "L1_cluster_review": {"clusters": [{"component_id": "c1", "topic_action": "existing", "topic_slug": "existing-topic"}], "misc_event_ids": []},
            "L2_narrative": {"markdown": DAILY_MARKDOWN},
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)
    topics_dir = tmp_path / ".keypulse" / "topics"
    topics_dir.mkdir(parents=True)
    topics_dir.joinpath("existing-topic.md").write_text(
        "---\ntype: topic\nslug: existing-topic\ndisplay_name: KeyPulse Daily Strategy\nfirst_seen: 2026-04-30\nlast_seen: 2026-04-30\nkeywords:\n  - keypulse\n  - daily\n  - strategy\n  - tier\n  - budget\n---\n\n# KeyPulse Daily Strategy\n",
        encoding="utf-8",
    )

    run_daily("2026-05-01", trigger="18:00")

    assert gateway.calls == ["L1_cluster_review", "L2_narrative", "L0_anchor"]


def test_daily_strategy_error_is_translated(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="doubao-seed-1-6-250615")
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway("doubao-seed-1-6-250615", {"daily_flagship": {"markdown": ""}})
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    with pytest.raises(DailyOrchestratorError, match="empty markdown"):
        run_daily("2026-05-01", trigger="18:00")


def test_low_volume_skip_unchanged(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="doubao-seed-1-6-250615")
    _patch_io(monkeypatch, tmp_path, _rows()[:2])

    summary = run_daily("2026-05-01", trigger="18:00")

    assert summary.skipped is True
    assert summary.cluster_count == 0
    assert summary.misc_event_ids == ("1", "2")
    daily_body = Path(summary.daily_path).read_text(encoding="utf-8")
    assert "## 今日要点" in daily_body
    assert "## 今天做的事" in daily_body


def _insert_event(
    *,
    ts_start: str,
    content: str,
    session_id: str,
    app: str = "Codex",
    window: str = "KeyPulse",
    entities: dict | None = None,
) -> None:
    payload = {"entities": dict(entities or {})}
    payload["entities"]["session_id"] = session_id
    event = RawEvent(
        source="window",
        event_type="window_title_changed",
        ts_start=ts_start,
        app_name=app,
        window_title=window,
        content_text=content,
        metadata_json=json.dumps(payload, ensure_ascii=False),
        session_id=session_id,
    )
    insert_raw_event(event)


def _seed_minimal_events() -> None:
    _insert_event(ts_start="2026-05-01T01:00:00+00:00", content="实现 daily orchestrator 主流程", session_id="s1")
    _insert_event(ts_start="2026-05-01T01:03:00+00:00", content="补 L1 schema 并校验输出", session_id="s1")
    _insert_event(ts_start="2026-05-01T01:20:00+00:00", content="补 L2 narrative prompt", session_id="s2")


def test_daily_cli_falls_back_to_unified_renderer_when_mock_llm_keeps_failing(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_minimal_events()
    monkeypatch.setenv("MOCK_LLM_FAILS", "99")
    fallback_target = tmp_path / "vault" / "Daily" / "2026-05-01.md"

    def fake_fallback(_cfg, _date_str, *, no_llm):
        fallback_target.parent.mkdir(parents=True, exist_ok=True)
        fallback_target.write_text("# fallback\n\n## 今日要点\n\n- fallback\n", encoding="utf-8")
        return fallback_target

    monkeypatch.setattr("keypulse.cli._render_daily_fallback", fake_fallback)

    result = CliRunner().invoke(
        main,
        [
            "daily",
            "run",
            "--date",
            "2026-05-01",
            "--trigger",
            "18:00",
            "--mock-llm",
        ],
    )

    assert result.exit_code == 0
    assert "daily_run=fallback" in result.output
    assert "fallback_daily_path=" in result.output
    assert fallback_target.exists()
    close()
