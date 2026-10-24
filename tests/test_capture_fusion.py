from __future__ import annotations

import json

from penpal.capture.fusion import CaptureFusionEngine
from penpal.capture.normalizer import normalize_ax_text_event


def test_fusion_drops_lower_priority_duplicate_after_canonical_exists():
    engine = CaptureFusionEngine(similarity_threshold=0.9)

    canonical = normalize_ax_text_event(
        text="same body",
        app_name="Notes",
        window_title="Draft",
        ts_start="2026-04-19T09:00:00+00:00",
    )
    assert engine.fuse(canonical).persist is True

    duplicate = normalize_ax_text_event(
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

    canonical = normalize_ax_text_event(
        text="same body",
        app_name="Notes",
        window_title="Draft",
        ts_start="2026-04-19T09:00:00+00:00",
    )
    assert engine.fuse(canonical).persist is True

    # D27: identical text is now merged for up to 300s (Jaccard); the 15s exact window only
    # still applies to text that is not a near-duplicate.
    late_duplicate = normalize_ax_text_event(
        text="completely different words here",
        app_name="Notes",
        window_title="Draft",
        ts_start="2026-04-19T09:00:16+00:00",
    )
    result = engine.fuse(late_duplicate)

    assert result.persist is True
    assert result.event is not None
    metadata = json.loads(result.event.metadata_json or "{}")
    assert metadata.get("merge_reason") is None
