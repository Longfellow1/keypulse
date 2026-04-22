from __future__ import annotations

import json

from keypulse.capture.fusion import CaptureFusionEngine
from keypulse.capture.normalizer import (
    normalize_ax_text_event,
    normalize_keyboard_chunk_event,
)


def test_fusion_promotes_high_priority_source_and_carries_aux_sources():
    engine = CaptureFusionEngine(similarity_threshold=0.9)

    lower = normalize_keyboard_chunk_event(
        text="hello fused world",
        app_name="Notes",
        window_title="Draft",
        ts_start="2026-04-19T09:00:00+00:00",
    )
    lower_result = engine.fuse(lower)

    assert lower_result.persist is True
    assert lower_result.event is not None

    higher = normalize_ax_text_event(
        text="hello fused world",
        app_name="Notes",
        window_title="Draft",
        ts_start="2026-04-19T09:00:05+00:00",
    )
    higher_result = engine.fuse(higher)

    assert higher_result.persist is True
    assert higher_result.event is not None
    metadata = json.loads(higher_result.event.metadata_json or "{}")
    assert metadata["merge_reason"] == "replaced_lower_priority_duplicate"
    assert metadata["sources"] == ["keyboard_chunk", "ax_text"]


def test_fusion_drops_lower_priority_duplicate_after_canonical_exists():
    engine = CaptureFusionEngine(similarity_threshold=0.9)

    canonical = normalize_ax_text_event(
        text="same body",
        app_name="Notes",
        window_title="Draft",
        ts_start="2026-04-19T09:00:00+00:00",
    )
    assert engine.fuse(canonical).persist is True

    duplicate = normalize_keyboard_chunk_event(
        text="same body",
        app_name="Notes",
        window_title="Draft",
        ts_start="2026-04-19T09:00:03+00:00",
    )
    result = engine.fuse(duplicate)

    assert result.persist is False
    assert result.event is None
    assert result.reason == "lower_priority_duplicate"


def test_fusion_does_not_merge_events_16_seconds_apart():
    engine = CaptureFusionEngine(similarity_threshold=0.9)

    canonical = normalize_keyboard_chunk_event(
        text="same body",
        app_name="Notes",
        window_title="Draft",
        ts_start="2026-04-19T09:00:00+00:00",
    )
    assert engine.fuse(canonical).persist is True

    late_duplicate = normalize_ax_text_event(
        text="same body",
        app_name="Notes",
        window_title="Draft",
        ts_start="2026-04-19T09:00:16+00:00",
    )
    result = engine.fuse(late_duplicate)

    assert result.persist is True
    assert result.event is not None
    metadata = json.loads(result.event.metadata_json or "{}")
    assert metadata.get("merge_reason") is None
