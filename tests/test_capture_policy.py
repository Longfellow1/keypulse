from __future__ import annotations

from keypulse.capture.policy import METADATA_ONLY, PolicyEngine
from keypulse.store.models import RawEvent


def test_metadata_only_policy_returns_new_event_without_leaking_window_or_content():
    engine = PolicyEngine()
    engine._policies = [
        {
            "scope_type": "app",
            "scope_value": "Terminal",
            "mode": METADATA_ONLY,
            "enabled": True,
            "priority": 1,
        }
    ]

    original = RawEvent(
        source="manual",
        event_type="manual_save",
        ts_start="2026-04-18T09:00:00+00:00",
        app_name="Terminal",
        window_title="Secret project notes",
        content_text="Secret project notes",
        content_hash="abc123",
    )

    redacted = engine.apply(original)

    assert redacted is not None
    assert redacted is not original
    assert redacted.window_title is None
    assert redacted.content_text is None
    assert redacted.content_hash is None
    assert original.window_title == "Secret project notes"
    assert original.content_text == "Secret project notes"
    assert original.content_hash == "abc123"
