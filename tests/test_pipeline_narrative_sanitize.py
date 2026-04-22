from __future__ import annotations

import json

from keypulse.config import Config
from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.narrative import aggregate_work_blocks


def test_high_sensitivity_events_are_sanitized_in_work_blocks():
    blocks = aggregate_work_blocks(
        [
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "ts_end": "2026-04-18T09:08:00+00:00",
                "session_id": "session-sensitive",
                "app_name": "Notes",
                "window_title": "Secret draft",
                "title": "Secret draft",
                "body": "private roadmap",
                "content_text": "private roadmap",
                "evidence": "private roadmap",
                "sensitivity_level": 2,
            }
        ],
        sessions=[
            {
                "id": "session-sensitive",
                "started_at": "2026-04-18T09:00:00+00:00",
                "ended_at": "2026-04-18T09:08:00+00:00",
                "app_name": "Notes",
                "primary_window_title": "Secret draft",
                "duration_sec": 480,
                "event_count": 1,
            }
        ],
    )

    serialized = json.dumps([block.__dict__ for block in blocks], ensure_ascii=False)

    assert len(blocks) == 1
    assert blocks[0].session_id == "session-sensitive"
    assert blocks[0].key_candidates[0]["title"] == "<高敏内容 · 已记录未展示>"
    assert blocks[0].key_candidates[0]["body"] == "<高敏内容 · 已记录未展示>"
    assert "private roadmap" not in serialized
    assert "Secret draft" not in serialized


def test_low_sensitivity_events_keep_original_content():
    blocks = aggregate_work_blocks(
        [
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T10:00:00+00:00",
                "session_id": "session-public",
                "app_name": "",
                "content_text": "public roadmap",
                "sensitivity_level": 0,
            }
        ]
    )

    assert len(blocks) == 1
    assert blocks[0].key_candidates[0]["title"] == "public roadmap"
    assert blocks[0].key_candidates[0]["body"] == "public roadmap"


def test_mixed_events_do_not_leak_sensitive_text_into_narrative_prompt(monkeypatch, tmp_path):
    blocks = aggregate_work_blocks(
        [
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "session_id": "session-sensitive",
                "app_name": "Notes",
                "window_title": "Secret draft",
                "title": "Secret draft",
                "body": "private roadmap",
                "content_text": "private roadmap",
                "sensitivity_level": 2,
            },
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T10:00:00+00:00",
                "session_id": "session-public",
                "app_name": "Notes",
                "content_text": "public roadmap",
                "sensitivity_level": 0,
            },
        ]
    )

    cfg = Config.model_validate(
        {
            "model": {
                "active_profile": "local-first",
                "state_path": str(tmp_path / "model-state.json"),
                "local": {
                    "kind": "lm_studio",
                    "base_url": "http://127.0.0.1:1234",
                    "model": "local-model",
                },
            }
        }
    )
    gateway = ModelGateway(cfg)
    seen: dict[str, str] = {}

    def fake_call_backend(backend, prompt, prompt_patch=""):
        seen["prompt"] = prompt
        seen["prompt_patch"] = prompt_patch
        return "rendered"

    monkeypatch.setattr(gateway, "_call_backend", fake_call_backend)

    rendered = gateway.render_daily_narrative(blocks)

    assert rendered == "rendered"
    assert "private roadmap" not in seen["prompt"]
    assert "Secret draft" not in seen["prompt"]
    assert "public roadmap" in seen["prompt"]
