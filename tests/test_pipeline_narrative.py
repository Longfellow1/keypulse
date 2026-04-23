from __future__ import annotations

from keypulse.pipeline.narrative import WorkBlock, aggregate_work_blocks, render_daily_narrative


def _event(*, ts_start: str, ts_end: str | None = None, session_id: str, app_name: str, topic_key: str, title: str) -> dict[str, str]:
    event = {
        "source": "manual",
        "event_type": "manual_save",
        "ts_start": ts_start,
        "session_id": session_id,
        "app_name": app_name,
        "title": title,
        "body": title,
        "topic_key": topic_key,
    }
    if ts_end is not None:
        event["ts_end"] = ts_end
    return event


def test_aggregate_work_blocks_groups_sessions_and_marks_fragments():
    blocks = aggregate_work_blocks(
        [
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "ts_end": "2026-04-18T09:20:00+00:00",
                "session_id": "session-1",
                "app_name": "Safari",
                "window_title": "ReAct paper",
                "title": "ReAct paper",
                "body": "整理 ReAct 的核心思路",
                "topic_key": "react-agent",
            },
            {
                "source": "clipboard",
                "event_type": "clipboard_copy",
                "ts_start": "2026-04-18T09:10:00+00:00",
                "ts_end": "2026-04-18T09:12:00+00:00",
                "session_id": "session-1",
                "app_name": "Obsidian",
                "title": "ReAct note",
                "body": "推理步骤可解释性",
                "topic_key": "react-agent",
            },
            {
                "source": "clipboard",
                "event_type": "clipboard_copy",
                "ts_start": "2026-04-18T10:00:00+00:00",
                "ts_end": "2026-04-18T10:02:00+00:00",
                "session_id": "session-2",
                "app_name": "Mail",
                "title": "Q1 note",
                "body": "资源重分配纪要",
                "topic_key": "q1-plan",
            },
        ],
        sessions=[
            {
                "id": "session-1",
                "started_at": "2026-04-18T09:00:00+00:00",
                "ended_at": "2026-04-18T09:20:00+00:00",
                "app_name": "Safari",
                "primary_window_title": "ReAct paper",
                "duration_sec": 1200,
                "event_count": 2,
            },
            {
                "id": "session-2",
                "started_at": "2026-04-18T10:00:00+00:00",
                "ended_at": "2026-04-18T10:02:00+00:00",
                "app_name": "Mail",
                "primary_window_title": "Q1 note",
                "duration_sec": 120,
                "event_count": 1,
            },
        ],
        recent_topic_keys={"react-agent"},
        previous_day_topic_keys={"react-agent"},
    )

    assert len(blocks) == 3
    assert blocks[0].theme == "react-agent"
    assert blocks[0].continuity == "continued"
    assert blocks[0].duration_sec == 1200
    assert blocks[0].primary_app == "Safari"
    assert blocks[1].theme == "react-agent"
    assert blocks[1].fragment is True
    assert blocks[2].theme == "q1-plan"
    assert blocks[2].fragment is True


def test_aggregate_work_blocks_merges_same_session_same_app_within_time_window_even_when_topic_differs():
    blocks = aggregate_work_blocks(
        [
            _event(
                ts_start="2026-04-18T09:00:00+00:00",
                ts_end="2026-04-18T09:06:00+00:00",
                session_id="session-1",
                app_name="Codex",
                topic_key="alpha",
                title="alpha",
            ),
            _event(
                ts_start="2026-04-18T09:03:00+00:00",
                ts_end="2026-04-18T09:09:00+00:00",
                session_id="session-1",
                app_name="Codex",
                topic_key="beta",
                title="beta",
            ),
        ]
    )

    assert len(blocks) == 1
    assert blocks[0].fragment is False
    assert blocks[0].event_count == 2
    assert set(blocks[0].subtopics) == {"alpha", "beta"}


def test_aggregate_work_blocks_splits_same_app_when_gap_exceeds_window():
    blocks = aggregate_work_blocks(
        [
            _event(
                ts_start="2026-04-18T09:00:00+00:00",
                ts_end="2026-04-18T09:06:00+00:00",
                session_id="session-1",
                app_name="Codex",
                topic_key="alpha",
                title="alpha",
            ),
            _event(
                ts_start="2026-04-18T09:12:30+00:00",
                ts_end="2026-04-18T09:18:00+00:00",
                session_id="session-1",
                app_name="Codex",
                topic_key="beta",
                title="beta",
            ),
        ]
    )

    assert len(blocks) == 2
    assert [block.event_count for block in blocks] == [1, 1]
    assert [block.fragment for block in blocks] == [False, False]


def test_aggregate_work_blocks_keeps_cross_app_switches_separate():
    blocks = aggregate_work_blocks(
        [
            _event(
                ts_start="2026-04-18T09:00:00+00:00",
                ts_end="2026-04-18T09:07:00+00:00",
                session_id="session-1",
                app_name="Codex",
                topic_key="alpha",
                title="alpha",
            ),
            _event(
                ts_start="2026-04-18T09:08:00+00:00",
                ts_end="2026-04-18T09:15:00+00:00",
                session_id="session-1",
                app_name="Browser",
                topic_key="beta",
                title="beta",
            ),
        ]
    )

    assert len(blocks) == 2
    assert [block.primary_app for block in blocks] == ["Codex", "Browser"]


def test_aggregate_work_blocks_marks_short_spans_as_fragments():
    blocks = aggregate_work_blocks(
        [
            _event(
                ts_start="2026-04-18T09:00:00+00:00",
                ts_end="2026-04-18T09:04:00+00:00",
                session_id="session-1",
                app_name="Codex",
                topic_key="alpha",
                title="alpha",
            )
        ]
    )

    assert len(blocks) == 1
    assert blocks[0].duration_sec == 240
    assert blocks[0].fragment is True


def test_aggregate_work_blocks_groups_sessionless_events_by_app():
    blocks = aggregate_work_blocks(
        [
            _event(
                ts_start="2026-04-18T09:00:00+00:00",
                ts_end="2026-04-18T09:04:00+00:00",
                session_id="",
                app_name="Codex",
                topic_key="alpha",
                title="alpha",
            ),
            _event(
                ts_start="2026-04-18T09:03:00+00:00",
                ts_end="2026-04-18T09:09:00+00:00",
                session_id="",
                app_name="Codex",
                topic_key="beta",
                title="beta",
            ),
            _event(
                ts_start="2026-04-18T09:05:00+00:00",
                ts_end="2026-04-18T09:12:00+00:00",
                session_id="",
                app_name="Codex",
                topic_key="gamma",
                title="gamma",
            ),
        ]
    )

    assert len(blocks) == 1
    assert blocks[0].primary_app == "Codex"
    assert blocks[0].event_count == 3
    assert blocks[0].fragment is False
    assert blocks[0].duration_sec == 720


def test_aggregate_work_blocks_splits_sessionless_across_apps():
    blocks = aggregate_work_blocks(
        [
            _event(
                ts_start="2026-04-18T09:00:00+00:00",
                ts_end="2026-04-18T09:06:00+00:00",
                session_id="",
                app_name="Codex",
                topic_key="alpha",
                title="alpha",
            ),
            _event(
                ts_start="2026-04-18T09:02:00+00:00",
                ts_end="2026-04-18T09:08:00+00:00",
                session_id="",
                app_name="Browser",
                topic_key="beta",
                title="beta",
            ),
        ]
    )

    assert len(blocks) == 2
    assert sorted(block.primary_app for block in blocks) == ["Browser", "Codex"]


def test_aggregate_work_blocks_sessionless_no_ts_end_uses_ts_start_for_duration():
    blocks = aggregate_work_blocks(
        [
            _event(ts_start="2026-04-18T09:00:00+00:00", session_id="", app_name="Codex", topic_key="a", title="a"),
            _event(ts_start="2026-04-18T09:03:00+00:00", session_id="", app_name="Codex", topic_key="b", title="b"),
            _event(ts_start="2026-04-18T09:06:00+00:00", session_id="", app_name="Codex", topic_key="c", title="c"),
            _event(ts_start="2026-04-18T09:09:00+00:00", session_id="", app_name="Codex", topic_key="d", title="d"),
        ]
    )

    assert len(blocks) == 1
    assert blocks[0].event_count == 4
    assert blocks[0].duration_sec == 540
    assert blocks[0].fragment is False


def test_aggregate_work_blocks_uses_window_focus_session_end_when_session_event_is_first():
    blocks = aggregate_work_blocks(
        [
            {
                "source": "window",
                "event_type": "window_focus_session",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "ts_end": "2026-04-18T09:10:00+00:00",
                "session_id": "session-1",
                "app_name": "Codex",
                "window_title": "main.py",
                "title": "main.py",
                "speaker": "system",
                "topic_key": "main",
            },
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:05:00+00:00",
                "session_id": "session-1",
                "app_name": "Codex",
                "window_title": "main.py",
                "title": "refactor session tracking",
                "content_text": "replace heartbeat with focus sessions",
                "speaker": "user",
                "topic_key": "session-tracking",
            },
        ]
    )

    assert len(blocks) == 1
    assert blocks[0].duration_sec == 600


def test_aggregate_work_blocks_falls_back_to_window_title_when_topic_is_placeholder():
    blocks = aggregate_work_blocks(
        [
            {
                "source": "window",
                "event_type": "window_focus",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "ts_end": "2026-04-18T09:07:00+00:00",
                "session_id": "",
                "app_name": "Terminal",
                "window_title": "keypulse review project status with haiku",
                "title": "keypulse review project status with haiku",
                "topic_key": "topic",
            },
            {
                "source": "window",
                "event_type": "window_focus",
                "ts_start": "2026-04-18T09:04:00+00:00",
                "ts_end": "2026-04-18T09:10:00+00:00",
                "session_id": "",
                "app_name": "Terminal",
                "window_title": "keypulse review project status with haiku",
                "title": "keypulse review project status with haiku",
                "topic_key": "topic",
            },
        ]
    )

    assert len(blocks) == 1
    assert "keypulse" in blocks[0].theme.lower()
    assert blocks[0].theme != "topic"


def test_render_daily_narrative_uses_evidence_formatter():
    blocks = [
        WorkBlock(
            theme="react-agent",
            duration_sec=1200,
            ts_start="2026-04-18T09:00:00+00:00",
            ts_end="2026-04-18T09:20:00+00:00",
            primary_app="Safari",
            event_count=2,
            key_candidates=[
                {
                    "title": "ReAct note",
                    "created_at": "2026-04-18T09:00:00+00:00",
                    "topic_key": "react-agent",
                }
            ],
            user_candidates=[
                {
                    "title": "ReAct note",
                    "created_at": "2026-04-18T09:00:00+00:00",
                    "topic_key": "react-agent",
                }
            ],
            system_candidates=[
                {
                    "title": "Safari tab",
                    "created_at": "2026-04-18T09:05:00+00:00",
                    "topic_key": "react-agent",
                }
            ],
            continuity="continued",
        )
    ]

    body = render_daily_narrative(blocks, evidence_formatter=lambda item: f"[[Events/{item['title']}]]")

    assert "## 今日主线" in body
    assert "**你做了什么**" in body
    assert "[[Events/ReAct note]]" in body
    assert "<summary>系统显示了什么（1 条）</summary>" in body
    assert "[[Events/Safari tab]]" in body


def test_render_daily_narrative_collapses_fragments_and_limits_subtopics():
    body = render_daily_narrative(
        [
            WorkBlock(
                theme="alpha",
                duration_sec=600,
                ts_start="2026-04-18T09:00:00+00:00",
                ts_end="2026-04-18T09:10:00+00:00",
                primary_app="Codex",
                event_count=2,
                key_candidates=[{"title": "alpha", "created_at": "2026-04-18T09:00:00+00:00", "topic_key": "alpha"}],
                user_candidates=[{"title": "alpha", "created_at": "2026-04-18T09:00:00+00:00", "topic_key": "alpha"}],
                continuity="new",
            ),
            WorkBlock(
                theme="碎片",
                duration_sec=120,
                ts_start="2026-04-18T09:10:00+00:00",
                ts_end="2026-04-18T09:12:00+00:00",
                primary_app="Codex",
                event_count=4,
                key_candidates=[{"title": "frag", "created_at": "2026-04-18T09:10:00+00:00", "topic_key": "frag"}],
                system_candidates=[{"title": "frag", "created_at": "2026-04-18T09:10:00+00:00", "topic_key": "frag"}],
                continuity="new",
                fragment=True,
            ),
        ],
        include_heading=False,
    )

    assert "### 碎片汇总" in body
    assert "另有 1 个零散片段" in body
    assert "<details>" not in body
    assert "**你做了什么**" in body
    assert "### 碎片 ·" not in body


def test_render_daily_narrative_avoids_self_reference_when_theme_equals_app():
    blocks = [
        WorkBlock(
            theme="终端",
            duration_sec=6600,
            ts_start="2026-04-20T09:00:00+00:00",
            ts_end="2026-04-20T10:50:00+00:00",
            primary_app="终端",
            event_count=5,
            key_candidates=[{"title": "coding", "created_at": "2026-04-20T09:00:00+00:00", "topic_key": "coding"}],
            continuity="continued",
        ),
        WorkBlock(
            theme="研究",
            duration_sec=3600,
            ts_start="2026-04-20T11:00:00+00:00",
            ts_end="2026-04-20T12:00:00+00:00",
            primary_app="Safari",
            event_count=3,
            key_candidates=[{"title": "papers", "created_at": "2026-04-20T11:00:00+00:00", "topic_key": "research"}],
            continuity="new",
        ),
    ]

    body = render_daily_narrative(blocks, include_heading=False)

    assert "### 终端 · 1h50m" in body
    assert "### 研究 · 1h00m" in body
    assert "你在 终端" not in body
