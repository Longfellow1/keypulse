from __future__ import annotations

from keypulse.pipeline.decisions import build_daily_decisions, render_daily_decisions
from keypulse.pipeline.feedback import FeedbackEvent
from keypulse.pipeline.narrative import WorkBlock


def test_build_daily_decisions_promotes_recurring_topics_and_defers_one_off_blocks():
    decisions = build_daily_decisions(
        [
            WorkBlock(
                theme="react-agent",
                duration_sec=1200,
                ts_start="2026-04-18T09:00:00+00:00",
                ts_end="2026-04-18T09:20:00+00:00",
                primary_app="Safari",
                event_count=3,
                key_candidates=[{"title": "ReAct note", "source": "manual"}],
                continuity="continued",
            ),
            WorkBlock(
                theme="vscode-autocomplete",
                duration_sec=240,
                ts_start="2026-04-18T10:00:00+00:00",
                ts_end="2026-04-18T10:04:00+00:00",
                primary_app="Safari",
                event_count=1,
                key_candidates=[{"title": "VSCode compare", "source": "clipboard"}],
                continuity="new",
            ),
            WorkBlock(
                theme="manual-note",
                duration_sec=180,
                ts_start="2026-04-18T11:00:00+00:00",
                ts_end="2026-04-18T11:03:00+00:00",
                primary_app="Obsidian",
                event_count=1,
                key_candidates=[{"title": "Manual note", "source": "manual"}],
                continuity="new",
            ),
        ],
        theme_keys={"react-agent"},
        recent_topic_counts={"react-agent": 4},
        feedback_events=[
            FeedbackEvent(kind="defer", target="vscode-autocomplete", note="continue tomorrow"),
            FeedbackEvent(kind="defer", target="vscode-autocomplete", note="still comparing"),
        ],
    )

    assert len(decisions) <= 3
    assert decisions[0].kind == "promote"
    assert decisions[0].command == "keypulse pipeline feedback promote react-agent"
    assert any(item.kind == "defer" and item.command == "keypulse pipeline feedback defer vscode-autocomplete" for item in decisions)
    assert any(item.kind == "archive" and item.command == "keypulse pipeline feedback archive manual-note" for item in decisions)


def test_render_daily_decisions_uses_command_style_markdown():
    body = render_daily_decisions(
        [
            {
                "kind": "promote",
                "title": "ReAct / Agent 范式",
                "reason": "已立主题，继续记录",
                "command": "keypulse pipeline feedback promote react-agent",
            }
        ]
    )

    assert "## 需要你决定" in body
    assert "keypulse pipeline feedback promote react-agent" in body
