from __future__ import annotations

from keypulse.pipeline.daily_strategy import extract_work_unit
from keypulse.pipeline.event_intake import SOURCE_INTAKE_WEIGHTS, cap_events_by_source, clean_events_semantic


def test_browser_url_weight_is_registered():
    assert SOURCE_INTAKE_WEIGHTS["browser_url"] == 1.0


def test_browser_url_event_survives_intake_cap_flow():
    event = {
        "id": "evt-1",
        "ts_start": "2026-05-19T10:00:00+08:00",
        "source": "browser_url",
        "speaker": "user",
        "app_name": "Google Chrome",
        "content_text": "KeyPulse brief",
        "metadata": {
            "entities": {
                "urls": ["https://chat.openai.com/c/abc123"],
            }
        },
    }

    cleaned = clean_events_semantic([event])
    capped, limited = cap_events_by_source(cleaned, limit=1)

    assert limited is False
    assert len(capped) == 1
    assert capped[0]["source"] == "browser_url"


def test_extract_work_unit_falls_back_to_host_and_first_path_segment_for_url():
    event = {
        "source": "browser_url",
        "metadata": {
            "entities": {
                "urls": ["https://chat.openai.com/c/abc123?model=gpt-5"],
            }
        },
    }

    assert extract_work_unit(event) == "chat.openai.com/c"
