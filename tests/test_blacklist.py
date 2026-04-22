from __future__ import annotations

from keypulse.capture.manager import CaptureManager
from keypulse.config import Config
from keypulse.store.models import RawEvent


def test_blacklisted_exact_bundle_id_matches():
    config = Config.model_validate(
        {
            "privacy": {
                "blacklist_bundle_ids": ["com.agilebits.onepassword7"],
                "blacklist_patterns": [],
            }
        }
    )
    manager = CaptureManager(config)

    event = RawEvent(
        source="window",
        event_type="window_focus",
        ts_start="2026-04-18T09:00:00+00:00",
        process_name="com.agilebits.onepassword7",
    )

    assert manager._is_blacklisted(event) is True


def test_blacklisted_glob_pattern_matches_tencent_variants():
    config = Config.model_validate(
        {
            "privacy": {
                "blacklist_bundle_ids": [],
                "blacklist_patterns": ["com.tencent.*"],
            }
        }
    )
    manager = CaptureManager(config)

    event = RawEvent(
        source="window",
        event_type="window_focus",
        ts_start="2026-04-18T09:00:00+00:00",
        process_name="com.tencent.wechat",
    )

    assert manager._is_blacklisted(event) is True


def test_blacklisted_event_falls_back_to_app_name_when_process_name_missing():
    config = Config.model_validate(
        {
            "privacy": {
                "blacklist_bundle_ids": ["com.agilebits.onepassword7"],
                "blacklist_patterns": [],
            }
        }
    )
    manager = CaptureManager(config)

    event = RawEvent(
        source="window",
        event_type="window_focus",
        ts_start="2026-04-18T09:00:00+00:00",
        app_name="com.agilebits.onepassword7",
    )

    assert manager._is_blacklisted(event) is True


def test_blacklisted_event_does_not_match_unrelated_apps():
    config = Config.model_validate(
        {
            "privacy": {
                "blacklist_bundle_ids": ["com.agilebits.onepassword7"],
                "blacklist_patterns": ["com.tencent.*"],
            }
        }
    )
    manager = CaptureManager(config)

    event = RawEvent(
        source="window",
        event_type="window_focus",
        ts_start="2026-04-18T09:00:00+00:00",
        process_name="com.apple.Notes",
        app_name="Notes",
    )

    assert manager._is_blacklisted(event) is False


def test_blacklisted_event_is_dropped_before_policy_and_persistence(monkeypatch):
    manager = CaptureManager(Config())
    event = RawEvent(
        source="window",
        event_type="window_focus",
        ts_start="2026-04-18T09:00:00+00:00",
        app_name="1Password 7",
        process_name="com.agilebits.onepassword7",
        window_title="Vault",
    )
    inserts: list[RawEvent] = []

    monkeypatch.setattr(
        manager._policy,
        "apply",
        lambda _event: (_ for _ in ()).throw(AssertionError("policy should not run")),
    )
    monkeypatch.setattr(
        "keypulse.capture.manager.insert_raw_event",
        lambda raw_event: inserts.append(raw_event),
    )

    manager._process_event(event)

    assert inserts == []


def test_window_title_is_desensitized_before_persistence(monkeypatch):
    manager = CaptureManager(Config())
    captured: list[RawEvent] = []
    event = RawEvent(
        source="window",
        event_type="window_focus",
        ts_start="2026-04-18T09:00:00+00:00",
        app_name="Notes",
        process_name="com.apple.Notes",
        window_title="alice@example.com",
    )

    monkeypatch.setattr(manager._aggregator, "process", lambda _event: None)
    monkeypatch.setattr(manager._policy, "apply", lambda raw_event: raw_event)
    monkeypatch.setattr(
        "keypulse.capture.manager.insert_raw_event",
        lambda raw_event: captured.append(raw_event) or 1,
    )
    monkeypatch.setattr("keypulse.capture.manager.set_state", lambda *_args, **_kwargs: None)

    manager._process_event(event)

    assert captured
    assert captured[0].window_title == "[EMAIL]"
