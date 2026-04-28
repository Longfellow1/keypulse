from __future__ import annotations

from datetime import datetime, timezone

from keypulse.pipeline.event_assigner import assign_events
from keypulse.pipeline.outline_prompt import ThingOutline
from keypulse.sources.types import SemanticEvent


def _event(intent: str, artifact: str = "") -> SemanticEvent:
    return SemanticEvent(
        time=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        source="claude_code",
        actor="Harland",
        intent=intent,
        artifact=artifact,
        raw_ref="",
        privacy_tier="green",
        metadata={},
    )


def test_assign_events_routes_to_best_outline_and_other_bucket() -> None:
    outlines = [
        ThingOutline(title="修复 timeline 隐私回归", summary_hint="调整匿名化与回归测试"),
        ThingOutline(title="实现 session splitter", summary_hint="按空闲 30 分钟切分活动"),
    ]
    events = [
        _event("修复 timeline 隐私回归并补测试", "keypulse/pipeline/things.py"),
        _event("新增 session splitter 并处理空事件", "keypulse/pipeline/session_splitter.py"),
        _event("中午散步吃饭", ""),
    ]

    assigned = assign_events(events, outlines)

    assert [event.intent for event in assigned["修复 timeline 隐私回归"]] == ["修复 timeline 隐私回归并补测试"]
    assert [event.intent for event in assigned["实现 session splitter"]] == ["新增 session splitter 并处理空事件"]
    assert [event.intent for event in assigned["_其他"]] == ["中午散步吃饭"]


def test_assign_events_picks_highest_similarity_when_multiple_match() -> None:
    outlines = [
        ThingOutline(title="修复 timeline", summary_hint="timeline 回归"),
        ThingOutline(title="修复 timeline 隐私回归", summary_hint="匿名化策略"),
    ]
    event = _event("修复 timeline 隐私回归并调整匿名化策略")

    assigned = assign_events([event], outlines)

    assert assigned["修复 timeline"] == []
    assert assigned["修复 timeline 隐私回归"] == [event]


def test_assign_events_handles_empty_outlines() -> None:
    event = _event("修复 timeline")
    assigned = assign_events([event], [])
    assert assigned == {"_其他": [event]}
