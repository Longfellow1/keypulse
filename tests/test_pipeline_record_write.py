from __future__ import annotations

from keypulse.pipeline.contracts import PipelineInputs
from keypulse.pipeline.record import normalize_record_events
from keypulse.pipeline.write import build_daily_draft


def test_normalize_record_events_prefers_manual_events_at_same_timestamp():
    events = [
        {
            "source": "clipboard",
            "event_type": "clipboard_copy",
            "ts_start": "2026-04-18T09:00:00+00:00",
            "title": "Copied snippet",
            "body": "return value = True",
        },
        {
            "source": "manual",
            "event_type": "manual_save",
            "ts_start": "2026-04-18T09:00:00+00:00",
            "title": "Manual note",
            "body": "design the pipeline first",
        },
    ]

    normalized = normalize_record_events(events)

    assert [item.title for item in normalized] == ["Manual note", "Copied snippet"]
    assert normalized[0].importance > normalized[1].importance


def test_normalize_record_events_uses_manual_body_excerpt_as_title():
    normalized = normalize_record_events(
        [
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "content_text": "修复 retention 启动崩溃，避免 VACUUM 在事务内执行。",
            }
        ]
    )

    assert len(normalized) == 1
    assert normalized[0].title == "修复 retention 启动崩溃，避免 VACUUM 在事务内执行。"


def test_normalize_record_events_filters_idle_and_low_signal_window_events():
    normalized = normalize_record_events(
        [
            {
                "source": "window",
                "event_type": "window_focus",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "app_name": "终端",
                "window_title": "终端",
            },
            {
                "source": "idle",
                "event_type": "idle_start",
                "ts_start": "2026-04-18T09:01:00+00:00",
            },
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:02:00+00:00",
                "content_text": "保留真正有意义的手工笔记。",
            },
        ]
    )

    assert [item.title for item in normalized] == ["保留真正有意义的手工笔记。"]


def test_build_daily_draft_is_deterministic_without_llm():
    inputs = PipelineInputs(event_count=2, candidate_count=1, topic_count=0, active_days=1)
    draft = build_daily_draft(
        inputs,
        [
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "title": "Fix install path",
                "body": "install.sh now reuses the venv",
            },
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:10:00+00:00",
                "title": "Add sink detect",
                "body": "autobind to the best local vault",
            },
        ],
    )

    assert "碎片汇总" in draft.body
    assert "另有 2 个零散片段" in draft.body
    assert draft.llm_used is False
    assert draft.event_count == 2
