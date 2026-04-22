from __future__ import annotations

from keypulse.pipeline.narrative import (
    WorkBlock,
    _build_work_block,
    _event_speaker,
    _pick_primary_topic,
    _render_block_lines,
)


def _event(
    *,
    ts_start: str,
    ts_end: str | None = None,
    source: str,
    app_name: str,
    title: str,
    body: str = "",
    topic_key: str = "",
    speaker: str = "",
) -> dict[str, str]:
    event = {
        "source": source,
        "event_type": f"{source}_event",
        "ts_start": ts_start,
        "app_name": app_name,
        "title": title,
        "body": body or title,
    }
    if ts_end is not None:
        event["ts_end"] = ts_end
    if topic_key:
        event["topic_key"] = topic_key
    if speaker:
        event["speaker"] = speaker
    return event


def test_event_speaker_prefers_explicit_speaker_and_falls_back_to_source():
    assert _event_speaker({"speaker": "user", "source": "window"}) == "user"
    assert _event_speaker({"speaker": "system", "source": "clipboard"}) == "system"
    assert _event_speaker({"source": "keyboard_chunk"}) == "user"
    assert _event_speaker({"source": "window"}) == "system"


def test_build_work_block_splits_user_and_system_candidates_and_prefers_user_theme():
    events = [
        _event(
            ts_start="2026-04-20T09:00:00+00:00",
            ts_end="2026-04-20T09:03:00+00:00",
            source="keyboard_chunk",
            app_name="Codex",
            title="user alpha",
            body="用户 alpha",
            topic_key="user-topic",
            speaker="user",
        ),
        _event(
            ts_start="2026-04-20T09:01:00+00:00",
            ts_end="2026-04-20T09:04:00+00:00",
            source="clipboard",
            app_name="Codex",
            title="user beta",
            body="用户 beta",
            topic_key="user-topic",
        ),
        _event(
            ts_start="2026-04-20T09:02:00+00:00",
            ts_end="2026-04-20T09:05:00+00:00",
            source="window",
            app_name="Safari",
            title="system alpha",
            body="系统 alpha",
            topic_key="system-topic",
        ),
        _event(
            ts_start="2026-04-20T09:03:00+00:00",
            ts_end="2026-04-20T09:06:00+00:00",
            source="window",
            app_name="Safari",
            title="system beta",
            body="系统 beta",
            topic_key="system-topic",
        ),
        _event(
            ts_start="2026-04-20T09:04:00+00:00",
            ts_end="2026-04-20T09:07:00+00:00",
            source="window",
            app_name="Safari",
            title="system gamma",
            body="系统 gamma",
            topic_key="system-topic",
        ),
    ]

    block = _build_work_block(
        events,
        session_id="session-1",
        session_by_id=None,
        recent_topic_keys=None,
        previous_day_topic_keys=None,
    )

    assert block.theme == "user-topic"
    assert len(block.user_candidates) > 0
    assert len(block.system_candidates) > 0
    assert any(candidate["source"] in {"keyboard_chunk", "clipboard"} for candidate in block.user_candidates)
    assert any(candidate["source"] == "window" for candidate in block.system_candidates)


def test_build_work_block_marks_system_only_block_as_fragment():
    block = _build_work_block(
        [
            _event(
                ts_start="2026-04-20T09:00:00+00:00",
                ts_end="2026-04-20T09:02:00+00:00",
                source="window",
                app_name="Safari",
                title="heartbeat 1",
                body="heartbeat 1",
                topic_key="system-topic",
            ),
            _event(
                ts_start="2026-04-20T09:02:30+00:00",
                ts_end="2026-04-20T09:04:00+00:00",
                source="window",
                app_name="Safari",
                title="heartbeat 2",
                body="heartbeat 2",
                topic_key="system-topic",
            ),
        ],
        session_id="session-system-only",
        session_by_id=None,
        recent_topic_keys=None,
        previous_day_topic_keys=None,
    )

    assert block.fragment is True
    assert block.user_candidates == []
    assert len(block.system_candidates) == 2


def test_build_work_block_keeps_user_only_block_non_fragment_and_empty_system_candidates():
    block = _build_work_block(
        [
            _event(
                ts_start="2026-04-20T10:00:00+00:00",
                ts_end="2026-04-20T10:03:00+00:00",
                source="keyboard_chunk",
                app_name="Codex",
                title="draft 1",
                body="draft 1",
                topic_key="user-topic",
                speaker="user",
            ),
            _event(
                ts_start="2026-04-20T10:03:30+00:00",
                ts_end="2026-04-20T10:05:30+00:00",
                source="clipboard",
                app_name="Codex",
                title="draft 2",
                body="draft 2",
                topic_key="user-topic",
                speaker="user",
            ),
            _event(
                ts_start="2026-04-20T10:06:00+00:00",
                ts_end="2026-04-20T10:08:00+00:00",
                source="manual",
                app_name="Codex",
                title="draft 3",
                body="draft 3",
                topic_key="user-topic",
                speaker="user",
            ),
        ],
        session_id="session-user-only",
        session_by_id=None,
        recent_topic_keys=None,
        previous_day_topic_keys=None,
    )

    assert block.fragment is False
    assert len(block.user_candidates) == 3
    assert block.system_candidates == []


def test_render_block_lines_uses_dual_column_structure_and_omits_empty_system_details():
    block = WorkBlock(
        theme="user-topic",
        duration_sec=600,
        ts_start="2026-04-20T09:00:00+00:00",
        ts_end="2026-04-20T09:10:00+00:00",
        primary_app="Codex",
        event_count=4,
        key_candidates=[
            {"title": "user alpha", "source": "keyboard_chunk"},
        ],
        user_candidates=[
            {"title": "user alpha", "source": "keyboard_chunk"},
            {"title": "user beta", "source": "clipboard"},
        ],
        system_candidates=[],
        continuity="new",
    )

    lines = _render_block_lines(block, evidence_formatter=lambda item: f"[[{item['title']}]]")
    body = "\n".join(lines)

    assert "**你做了什么**" in body
    assert "- [[user alpha]]" in body
    assert "<details>" not in body


def test_render_block_lines_renders_system_details_with_custom_formatter():
    block = WorkBlock(
        theme="system-topic",
        duration_sec=900,
        ts_start="2026-04-20T11:00:00+00:00",
        ts_end="2026-04-20T11:15:00+00:00",
        primary_app="Safari",
        event_count=5,
        key_candidates=[
            {"title": "system alpha", "source": "window"},
        ],
        user_candidates=[
            {"title": "user alpha", "source": "keyboard_chunk"},
        ],
        system_candidates=[
            {"title": "system alpha", "source": "window"},
            {"title": "system beta", "source": "window"},
        ],
        continuity="continued",
    )

    body = "\n".join(_render_block_lines(block, evidence_formatter=lambda item: f"[[{item['title']}]]"))

    assert "**你做了什么**" in body
    assert "<details>" in body
    assert "系统显示了什么（2 条）" in body
    assert "- [[system alpha]]" in body
    assert "- [[system beta]]" in body

