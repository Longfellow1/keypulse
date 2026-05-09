#!/usr/bin/env python3
"""Minimal benchmark for M0 entity extraction overhead (100 events)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import keypulse.sources.sink as sink
from keypulse.sources.types import SemanticEvent


def _build_events(total: int = 100) -> list[SemanticEvent]:
    base = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
    events: list[SemanticEvent] = []
    for idx in range(total):
        source = ("git_log", "claude_code", "chrome_history")[idx % 3]
        metadata = {
            "session_id": f"s-{idx}",
            "repo_path": "/Users/harland/Go/keypulse",
            "full_hash": f"abc1234def{idx:04d}",
            "full_url": f"https://example.com/docs/{idx}?token=secret",
        }
        events.append(
            SemanticEvent(
                time=base + timedelta(seconds=idx),
                source=source,
                actor="user",
                intent=f"修复 keypulse pipeline bug #{idx}",
                artifact=f"commit:abc1234 file:keypulse/pipeline/sink_{idx}.py",
                raw_ref=f"{source}:{idx}",
                privacy_tier="green",
                metadata=metadata,
            )
        )
    return events


def _measure(events: list[SemanticEvent]) -> float:
    start = time.perf_counter()
    for event in events:
        sink.semantic_event_to_raw_event(event)
    return time.perf_counter() - start


def main() -> int:
    events = _build_events(100)

    original_extractor = sink.extract_named_entities
    try:
        sink.extract_named_entities = lambda event: []  # type: ignore[assignment]
        baseline = _measure(events)
    finally:
        sink.extract_named_entities = original_extractor  # type: ignore[assignment]

    with_extractor = _measure(events)
    overhead = ((with_extractor - baseline) / baseline * 100.0) if baseline > 0 else 0.0

    print("[benchmark_m0_entities]")
    print(f"events=100")
    print(f"baseline_without_entity_extractor={baseline * 1000:.3f} ms")
    print(f"with_entity_extractor={with_extractor * 1000:.3f} ms")
    print(f"overhead={overhead:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
