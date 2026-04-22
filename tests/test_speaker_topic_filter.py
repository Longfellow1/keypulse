from __future__ import annotations

import json
from pathlib import Path

import pytest

from keypulse.obsidian.exporter import _meaningful_item, _topic_from_item, build_obsidian_bundle


GOLDEN_PATH = Path(__file__).parent / "golden" / "speaker_filtering.jsonl"


def _load_samples():
    samples = []
    for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        samples.append(json.loads(line))
    return samples


@pytest.mark.parametrize("sample", _load_samples(), ids=lambda s: s.get("note") or s["event"]["source"])
def test_speaker_filtering_golden(sample):
    assert _meaningful_item(sample["event"]) is sample["expected_meaningful"]


def test_topic_generation_ignores_system_window_events():
    bundle = build_obsidian_bundle(
        [
            {
                "created_at": "2026-04-20T09:00:00+00:00",
                "ts_start": "2026-04-20T09:00:00+00:00",
                "ts_end": "2026-04-20T09:10:00+00:00",
                "source": "window",
                "speaker": "system",
                "event_type": "window_title_changed",
                "title": "终端",
                "window_title": "终端",
                "body": "window heartbeat",
                "app_name": "Terminal",
                "session_id": "window-session",
            },
            {
                "created_at": "2026-04-20T09:12:00+00:00",
                "ts_start": "2026-04-20T09:12:00+00:00",
                "ts_end": "2026-04-20T09:18:00+00:00",
                "source": "keyboard_chunk",
                "speaker": "user",
                "event_type": "keyboard_chunk",
                "body": "docker compose config",
                "content_text": "docker compose config",
                "app_name": "Terminal",
                "session_id": "keyboard-session",
            },
            {
                "created_at": "2026-04-20T09:22:00+00:00",
                "ts_start": "2026-04-20T09:22:00+00:00",
                "ts_end": "2026-04-20T09:28:00+00:00",
                "source": "clipboard",
                "speaker": "user",
                "event_type": "clipboard_copy",
                "body": "docker compose config",
                "content_text": "docker compose config",
                "app_name": "Terminal",
                "session_id": "clipboard-session",
            },
        ],
        vault_name="Harland Knowledge",
        date_str="2026-04-20",
        sessions=[
            {"id": "window-session", "duration_sec": 600},
            {"id": "keyboard-session", "duration_sec": 360},
            {"id": "clipboard-session", "duration_sec": 360},
        ],
    )

    assert bundle["dashboard"][0]["properties"]["top_theme"] == "docker-compose-config"
    assert bundle["daily"][0]["properties"]["topic_count"] == 1
    assert len(bundle["topics"]) == 1
    assert _topic_from_item({"app_name": "终端", "body": "docker compose config"}) == "docker-compose-config"
    assert _topic_from_item({"app_name": "终端"}) is None
