from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate


_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "keypulse" / "prompts" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_l4_schema_valid_and_invalid():
    valid_input = {
        "scope_week": "2026-W18",
        "daily_topic_status_snapshots": [
            {"date": "2026-05-04", "topic_status_snapshot": {"keypulse-hud-fix": {"name": "HUD 权限修复", "state": "completed"}}}
        ],
        "topics": [
            {
                "slug": "keypulse-hud-fix",
                "name": "HUD 权限修复",
                "state": "completed",
                "weekly_entries": [{"date": "2026-05-04", "narrative_one_line": "修权限", "event_count": 1}],
            }
        ],
        "event_counts": {"keypulse-hud-fix": 1},
        "cross_week_diff": [{"topic": "HUD 权限修复", "from_state": "started", "to_state": "in_progress"}],
        "key_decisions": [{"date": "2026-05-04", "text": "拍板修 HUD 权限路径", "source": "tag"}],
        "visible_outputs": [{"date": "2026-05-04", "text": "上线权限修复提示", "source": "tag"}],
    }
    validate(valid_input, _schema("L4_input.json"))

    valid_output = [
        {"slug": "keypulse-hud-fix", "name": "HUD 权限修复", "state": "completed", "weekly_entries": []}
    ]
    validate(valid_output, _schema("L4_output.json"))

    invalid_output = [{"slug": "a", "name": "", "state": "done", "weekly_entries": []}]
    with pytest.raises(ValidationError):
        validate(invalid_output, _schema("L4_output.json"))


def test_l5_schema_valid_and_invalid():
    valid_input = {
        "scope_week": "2026-W18",
        "topic": {
            "slug": "keypulse-weekly",
            "name": "Weekly 主线",
            "state": "in_progress",
            "weekly_entries": [{"date": "2026-05-04", "narrative_one_line": "推进 weekly"}],
        },
        "evidence": [{"date": "2026-05-04", "text": "推进 weekly"}],
        "previous_week_narrative": None,
        "cross_week_diff": [{"topic": "Weekly 主线", "from_state": "started", "to_state": "in_progress"}],
        "key_decisions": [{"date": "2026-05-04", "text": "决定按主线写周报", "source": "tag"}],
        "visible_outputs": [{"date": "2026-05-04", "text": "weekly markdown 初稿", "source": "tag"}],
        "tagged_blockers": [{"date": "2026-05-04", "text": "卡在 schema 校验", "source": "tag"}],
    }
    validate(valid_input, _schema("L5_input.json"))

    valid_output = {
        "slug": "keypulse-weekly",
        "narrative": "本周这条线持续推进，周中几次集中处理并完成关键链路 [[2026-05-04]]。",
        "anchors": ["[[2026-05-04]]"],
        "decisions": ["确认周报主题归并路径"],
        "outputs": ["生成 weekly markdown"],
        "blockers": [],
    }
    validate(valid_output, _schema("L5_output.json"))

    invalid_output = {"slug": "keypulse-weekly", "narrative": "短", "anchors": [], "decisions": [], "outputs": [], "blockers": []}
    with pytest.raises(ValidationError):
        validate(invalid_output, _schema("L5_output.json"))


def test_l5_input_schema_accepts_optional_weekly_entity_fields_and_keeps_legacy_shape():
    legacy_input = {
        "scope_week": "2026-W18",
        "topic": {
            "slug": "keypulse-weekly",
            "name": "Weekly 主线",
            "state": "in_progress",
            "weekly_entries": [{"date": "2026-05-04", "narrative_one_line": "推进 weekly"}],
        },
        "evidence": [{"date": "2026-05-04", "text": "推进 weekly"}],
        "previous_week_narrative": None,
        "cross_week_diff": [{"topic": "Weekly 主线", "from_state": "started", "to_state": "in_progress"}],
        "key_decisions": [{"date": "2026-05-04", "text": "决定按主线写周报", "source": "tag"}],
        "visible_outputs": [{"date": "2026-05-04", "text": "weekly markdown 初稿", "source": "tag"}],
        "tagged_blockers": [{"date": "2026-05-04", "text": "卡在 schema 校验", "source": "tag"}],
    }
    validate(legacy_input, _schema("L5_input.json"))

    with_entities = {
        **legacy_input,
        "topic": {
            "slug": "keypulse-weekly",
            "name": "Weekly 主线",
            "state": "in_progress",
            "weekly_entries": [
                {
                    "date": "2026-05-04",
                    "event_count": 4,
                    "narrative_one_line": "推进 weekly",
                    "activity_intensity": "medium",
                }
            ],
        },
        "canonical_entities": [
            {
                "canonical_name": "KeyPulse",
                "type": "project",
                "aliases": ["keypulse"],
                "appears_on_dates": ["2026-05-04"],
                "merge_reasoning": "same workspace and context",
            }
        ],
        "event_entity_map": [
            {
                "event_id": "1",
                "primary_entity": "KeyPulse",
                "confidence": 0.93,
                "needs_review": False,
                "date": "2026-05-04",
            }
        ],
    }
    validate(with_entities, _schema("L5_input.json"))


def test_l5_input_schema_rejects_unknown_activity_intensity():
    payload = {
        "scope_week": "2026-W18",
        "topic": {
            "slug": "keypulse-weekly",
            "name": "Weekly 主线",
            "state": "in_progress",
            "weekly_entries": [
                {
                    "date": "2026-05-04",
                    "event_count": 2,
                    "narrative_one_line": "推进 weekly",
                    "activity_intensity": "extreme",
                }
            ],
        },
        "evidence": [{"date": "2026-05-04", "text": "推进 weekly"}],
        "previous_week_narrative": None,
        "cross_week_diff": [],
        "key_decisions": [],
        "visible_outputs": [],
        "tagged_blockers": [],
    }
    with pytest.raises(ValidationError):
        validate(payload, _schema("L5_input.json"))


def test_l6_schema_valid_and_invalid():
    valid_input = {
        "scope_week": "2026-W18",
        "weekly_dailies": [
            {"date": "2026-05-04", "content_full": "今天推进 weekly 主线并修复 HUD。"}
        ],
        "topic_status": [
            {
                "slug": "weekly-mainline",
                "display_name": "Weekly 主线",
                "status": "ongoing",
                "weekly_count": 3,
                "last_week_count": 2,
            }
        ],
        "hud_inputs_this_week": [{"date": "2026-05-04", "content": "今天想收敛 weekly"}],
        "mainline_sections": [{"slug": "weekly-mainline", "narrative": "推进 weekly 主线"}],
        "last_week_observation_text": None,
        "cross_week_diff": [{"topic": "Weekly 主线", "from_state": "started", "to_state": "in_progress"}],
        "key_decisions": [{"date": "2026-05-04", "text": "决定按主线写周报", "source": "tag"}],
        "visible_outputs": [{"date": "2026-05-04", "text": "weekly markdown 初稿", "source": "tag"}],
    }
    validate(valid_input, _schema("L6_input.json"))

    valid_output = {
        "dropped_balls": [
            {"what": "周一你说想收敛周报，后面没看到", "anchor_link": "[[2026-05-04]]"}
        ],
        "observation": {
            "text": "这周几次都在修同一块，是否还有更小的切分点?",
            "anchor_link": "[[2026-05-04]]",
            "anchor_quote": "今天推进 weekly 主线并修复 HUD",
        },
        "risks": [],
    }
    validate(valid_output, _schema("L6_output.json"))

    invalid_output = {
        "dropped_balls": "not-array",
        "observation": {"text": "bad", "anchor_link": None, "anchor_quote": None},
        "risks": [],
    }
    with pytest.raises(ValidationError):
        validate(invalid_output, _schema("L6_output.json"))
