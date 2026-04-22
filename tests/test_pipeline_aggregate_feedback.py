from __future__ import annotations

from keypulse.pipeline.aggregate import build_theme_summary
from keypulse.pipeline.feedback import FeedbackEvent, append_feedback_event, read_feedback_events, summarize_feedback_events


def test_build_theme_summary_groups_repeated_topics_and_orders_by_frequency():
    summary = build_theme_summary(
        [
            {"topic": "decision-making", "title": "Better selection rules"},
            {"topic": "decision-making", "title": "Daily budget policy"},
            {"topic": "method", "title": "Deterministic fallback"},
        ]
    )

    assert summary.theme_name == "general"
    assert summary.version == 1
    assert "cognitive upgrade" in summary.body
    assert "execution experience" in summary.body
    assert summary.topic_counts["decision-making"] == 2
    assert summary.llm_used is False


def test_feedback_events_round_trip_jsonl(tmp_path):
    path = tmp_path / "feedback.jsonl"
    event = FeedbackEvent(kind="promote", target="decision-making", note="repeat topic with evidence")

    append_feedback_event(path, event)
    events = read_feedback_events(path)

    assert len(events) == 1
    assert events[0].kind == "promote"
    assert events[0].target == "decision-making"


def test_summarize_feedback_events_groups_repeated_targets():
    summary = summarize_feedback_events(
        [
            FeedbackEvent(kind="promote", target="decision-making", note="repeat topic"),
            FeedbackEvent(kind="defer", target="vscode-autocomplete", note="keep digging"),
            FeedbackEvent(kind="defer", target="vscode-autocomplete", note="not done"),
        ]
    )

    assert "promote decision-making x1" in summary
    assert "defer vscode-autocomplete x2" in summary
