from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate


_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "keypulse" / "prompts" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_l1_input_schema_accepts_valid_payload():
    payload = {
        "scope_date": "2026-05-01",
        "trigger": "18:00",
        "components": [
            {
                "component_id": "c1",
                "event_ids": ["e1", "e2"],
                "time_range": ["09:00", "09:20"],
                "h1_entities": ["file:keypulse/pipeline/daily_orchestrator.py"],
                "h2_contexts": ["session:claude-s1"],
                "keywords": ["keypulse", "orchestrator"],
            }
        ],
        "merge_candidates": [["c1", "c2"]],
        "existing_topics_index": [
            {
                "slug": "keypulse-daily-orchestrator",
                "display_name": "daily 编排",
                "keywords": ["keypulse", "daily", "orchestrator"],
                "last_seen": "2026-04-30",
                "status": "active",
            }
        ],
        "hot_cache": ["keypulse-daily-orchestrator"],
        "hud_input_today": None,
    }
    validate(payload, _schema("L1_input.json"))


def test_l1_output_schema_rejects_missing_topic_slug_for_existing():
    payload = {
        "clusters": [
            {
                "component_id": "c1",
                "topic_action": "existing",
                "reason": "keywords match",
            }
        ],
        "misc_event_ids": [],
    }
    with pytest.raises(ValidationError):
        validate(payload, _schema("L1_output.json"))


def test_l2_output_schema_accepts_markdown_object_and_rejects_plain_string():
    input_payload = {
        "date": "2026-05-01",
        "clusters": [
            {
                "display_name": "Daily 编排主线",
                "topic_action": "new",
                "events": [
                    {"t": "09:00", "s": "ax_text", "a": "Codex", "c": "实现 strategy 分支", "sp": "user"},
                    {"t": "09:10", "s": "ax_text", "a": "Codex", "c": "一次性生成整篇日报", "sp": "ai"},
                ],
            }
        ],
        "misc_events": [{"t": "10:00", "s": "clipboard", "a": "Chrome", "c": "misc browsing"}],
    }
    validate(input_payload, _schema("L2_input.json"))

    valid = {"markdown": "早上在 Codex 里补了 daily orchestrator 的主链路，随后补了 schema 校验并修复了两个失败用例。这个输出超过最小长度，用来确认 L2 新版 schema 仍然要求返回 markdown 对象。"}
    validate(valid, _schema("L2_output.json"))

    invalid = "这是纯字符串不是对象"
    with pytest.raises(ValidationError):
        validate(invalid, _schema("L2_output.json"))


def test_daily_flagship_input_schema_accepts_scene_fingerprint_and_clusters():
    payload = {
        "date": "2026-05-18",
        "events": [
            {
                "t": "09:00",
                "s": "codex_cli",
                "a": "Codex",
                "c": "补日报现场指纹",
                "sp": "user",
                "eid": "e1",
                "sid": "s1",
                "win": "KeyPulse",
                "wu": "daily_strategy",
                "fp": ["/Users/Harland/Go/keypulse/keypulse/pipeline/daily_strategy.py"],
                "url": "https://github.com/example/keypulse",
            }
        ],
        "clusters": [
            {
                "display_name": "KeyPulse Daily P1",
                "dwell_minutes": 42.0,
                "revisit_count": 2,
                "cross_app_count": 3,
                "event_count": 5,
                "time_range": ["09:00", "09:42"],
                "key_excerpts": ["补日报现场指纹", "把 session_id 接到 prompt"],
            }
        ],
        "yesterday_anchor": "",
        "recent_topic_history": [],
    }

    validate(payload, _schema("daily_flagship_input.json"))


def test_l3_output_schema_valid_and_invalid_keywords_count():
    valid = {
        "slug": "keypulse-daily-orchestrator",
        "display_name": "Daily 编排主线",
        "keywords": ["keypulse", "daily", "orchestrator", "cluster", "topic"],
    }
    validate(valid, _schema("L3_output.json"))

    invalid = {
        "slug": "keypulse-daily-orchestrator",
        "display_name": "Daily 编排主线",
        "keywords": ["only", "four", "items", "here"],
    }
    with pytest.raises(ValidationError):
        validate(invalid, _schema("L3_output.json"))
