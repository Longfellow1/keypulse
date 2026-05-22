from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate


_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "keypulse" / "prompts" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def test_l7_principle_distillation_schema_valid_and_invalid() -> None:
    valid_input = {
        "date": "2026-05-21",
        "keyboard_chunks": [
            {
                "id": "evt-1",
                "ts_start": "2026-05-21T10:11:00+08:00",
                "app_name": "Codex",
                "window_title": "KeyPulse",
                "content": "当用户心智模型和交互结构对齐时，后续解释成本会明显下降。",
            }
        ],
        "known_principles": [
            {
                "principle_id": "existing-principle",
                "distilled": "先收敛意图，再收敛实现。",
            }
        ],
    }
    validate(valid_input, _schema("L7_principle_distillation_input.json"))

    invalid_input = {
        "date": "2026-05-21",
        "keyboard_chunks": [{"id": "evt-1", "content": "missing fields"}],
        "known_principles": [],
    }
    with pytest.raises(ValidationError):
        validate(invalid_input, _schema("L7_principle_distillation_input.json"))

    valid_output = {
        "candidates": [
            {
                "slug": "ux-mental-model-alignment",
                "kind": "principle",
                "distilled": "先让信息结构贴合用户心智模型，再做微交互优化。",
                "quote": "我们这次页面最卡的不是动画，而是用户不理解信息分组，先把心智模型对齐再谈动效。",
                "confidence": 0.86,
            }
        ]
    }
    validate(valid_output, _schema("L7_principle_distillation_output.json"))

    invalid_output = {
        "candidates": [
            {
                "slug": "not-kebab!",
                "kind": "principle",
                "distilled": "太短",
                "quote": "too short",
                "confidence": 2,
            }
        ]
    }
    with pytest.raises(ValidationError):
        validate(invalid_output, _schema("L7_principle_distillation_output.json"))
