from __future__ import annotations

from datetime import datetime, timedelta, timezone

from keypulse.pipeline.thing_clusterer import cluster
from keypulse.sources.types import SemanticEvent


def _event(
    minute: int,
    *,
    source: str = "git_log",
    intent: str = "",
    artifact: str = "",
    metadata: dict | None = None,
) -> SemanticEvent:
    return SemanticEvent(
        time=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=minute),
        source=source,
        actor="Harland",
        intent=intent,
        artifact=artifact,
        raw_ref="",
        privacy_tier="green",
        metadata=metadata or {},
    )


def test_cluster_empty_input_returns_empty() -> None:
    assert cluster([]) == []


def test_cluster_merges_by_time_window_and_jaccard() -> None:
    events = [
        _event(0, intent="实现 timeline cache", artifact="keypulse/pipeline/cache.py"),
        _event(20, source="claude_code", intent="修复 timeline bug", artifact="keypulse/pipeline/cache.py"),
    ]
    things = cluster(events)
    assert len(things) == 1
    assert len(things[0].events) == 2


def test_cluster_forces_merge_by_commit_across_time() -> None:
    events = [
        _event(0, source="git_log", artifact="commit:11a3a9b"),
        _event(240, source="claude_code", intent="讨论 11a3a9b 修复"),
    ]
    things = cluster(events)
    assert len(things) == 1
    assert len(things[0].events) == 2
    assert things[0].sources == {"git_log", "claude_code"}


def test_cluster_forces_merge_by_session_across_time() -> None:
    events = [
        _event(0, source="claude_code", intent="计划 S2", metadata={"session_id": "s-1"}),
        _event(180, source="zsh_history", intent="pytest", metadata={"session_id": "s-1"}),
    ]
    things = cluster(events)
    assert len(things) == 1
    assert len(things[0].events) == 2


def test_cluster_forces_merge_by_file_within_30_minutes() -> None:
    events = [
        _event(0, source="zsh_history", artifact="keypulse/pipeline/things.py"),
        _event(25, source="claude_code", intent="修改 keypulse/pipeline/things.py"),
    ]
    things = cluster(events)
    assert len(things) == 1


def test_cluster_single_event_orphan_kept() -> None:
    events = [_event(0, intent="孤立事件", artifact="README.md")]
    things = cluster(events)
    assert len(things) == 1
    assert len(things[0].events) == 1


def test_cluster_title_is_string_safe() -> None:
    events = [_event(0, intent="修复 timeline\nunsafe", artifact="commit:11a3a9b")]
    things = cluster(events)
    assert "\n" not in things[0].title


def test_cluster_splits_when_similarity_low() -> None:
    events = [
        _event(0, intent="实现 timeline", artifact="keypulse/pipeline/cache.py"),
        _event(10, intent="调研 Chrome", artifact="README.md"),
    ]
    things = cluster(events, threshold=0.8)
    assert len(things) == 2
