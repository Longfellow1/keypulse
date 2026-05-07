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
        "candidate_pairs": [
            {
                "slug_a": "keypulse-hud-fix",
                "slug_b": "hud-permission-fix",
                "affinity_score": 6.8,
                "topic_a_summary": {
                    "display_name": "HUD 权限修复",
                    "keywords": ["keypulse", "hud", "permission"],
                    "weekly_entries": [
                        {"date": "2026-05-04", "narrative_one_line": "修权限"}
                    ],
                },
                "topic_b_summary": {
                    "display_name": "HUD 可见性修复",
                    "keywords": ["hud", "accessibility", "permission"],
                    "weekly_entries": [
                        {"date": "2026-05-05", "narrative_one_line": "补校验"}
                    ],
                },
            }
        ],
    }
    validate(valid_input, _schema("L4_input.json"))

    valid_output = {
        "decisions": [
            {
                "slug_a": "keypulse-hud-fix",
                "slug_b": "hud-permission-fix",
                "action": "merge",
                "into": "keypulse-hud-fix",
                "reason": "same issue",
            }
        ]
    }
    validate(valid_output, _schema("L4_output.json"))

    invalid_output = {
        "decisions": [
            {
                "slug_a": "a",
                "slug_b": "b",
                "action": "merge",
                "reason": "missing into",
            }
        ]
    }
    with pytest.raises(ValidationError):
        validate(invalid_output, _schema("L4_output.json"))


def test_l5_schema_valid_and_invalid():
    valid_input = {
        "topics_to_write": [
            {
                "slug": "keypulse-weekly",
                "display_name": "Weekly 主线",
                "status": "accelerating",
                "weekly_entries": [
                    {"date": "2026-05-04", "narrative_one_line": "推进 weekly"}
                ],
                "previous_week_narrative": None,
            }
        ]
    }
    validate(valid_input, _schema("L5_input.json"))

    valid_output = {
        "narratives": [
            {
                "slug": "keypulse-weekly",
                "narrative": "本周这条线明显更频繁，周中几次集中推进并完成关键链路 [[2026-05-04]]。",
                "anchors": ["[[2026-05-04]]"],
            }
        ]
    }
    validate(valid_output, _schema("L5_output.json"))

    invalid_output = {
        "narratives": [
            {
                "slug": "keypulse-weekly",
                "narrative": "短",
                "anchors": [],
            }
        ]
    }
    with pytest.raises(ValidationError):
        validate(invalid_output, _schema("L5_output.json"))


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
        "last_week_observation_text": None,
    }
    validate(valid_input, _schema("L6_input.json"))

    valid_output = {
        "missed_balls": [
            {"what": "周一你说想收敛周报，后面没看到", "anchor_link": "[[2026-05-04]]"}
        ],
        "observation": {
            "text": "这周几次都在修同一块，是否还有更小的切分点?",
            "anchor_link": "[[2026-05-04]]",
            "anchor_quote": "今天推进 weekly 主线并修复 HUD",
        },
    }
    validate(valid_output, _schema("L6_output.json"))

    invalid_output = {
        "missed_balls": "not-array",
        "observation": {"text": "bad", "anchor_link": None, "anchor_quote": None},
    }
    with pytest.raises(ValidationError):
        validate(invalid_output, _schema("L6_output.json"))
