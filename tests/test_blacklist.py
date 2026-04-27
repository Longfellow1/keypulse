from __future__ import annotations

import json

from keypulse.capture.manager import CaptureManager
from keypulse.capture.normalizer import normalize_ax_text_event, normalize_browser_tab_event
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
        event_type="window_focus_session",
        ts_start="2026-04-18T09:00:00+00:00",
        ts_end="2026-04-18T09:03:00+00:00",
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
    monkeypatch.setattr("keypulse.capture.manager.insert_search_doc", lambda _doc: 1)
    monkeypatch.setattr("keypulse.capture.manager.set_state", lambda *_args, **_kwargs: None)

    manager._process_event(event)

    assert captured
    assert captured[0].window_title == "[REDACTED]"


def test_terminal_ax_text_is_dropped_and_marked(monkeypatch):
    manager = CaptureManager(Config())
    captured: list[RawEvent] = []
    event = normalize_ax_text_event(
        text="claude --dangerously-skip-permissions",
        app_name="Terminal",
        window_title="zsh",
        ts_start="2026-04-18T09:00:00+00:00",
        metadata={"workspace": "keypulse"},
    )

    monkeypatch.setattr(manager._aggregator, "process", lambda _event: None)
    monkeypatch.setattr(manager._policy, "apply", lambda raw_event: raw_event)
    monkeypatch.setattr(
        "keypulse.capture.manager.insert_raw_event",
        lambda raw_event: captured.append(raw_event) or 1,
    )
    monkeypatch.setattr(
        "keypulse.capture.manager.insert_search_doc",
        lambda _doc: (_ for _ in ()).throw(AssertionError("search doc should not be inserted")),
    )
    monkeypatch.setattr("keypulse.capture.manager.set_state", lambda *_args, **_kwargs: None)

    manager._process_event(event)

    assert captured
    persisted = captured[0]
    assert persisted.source == "ax_text"
    assert persisted.app_name == "Terminal"
    assert persisted.window_title == "zsh"
    assert persisted.ts_start == "2026-04-18T09:00:00+00:00"
    assert persisted.sensitivity_level == event.sensitivity_level
    assert persisted.content_text is None
    metadata = json.loads(persisted.metadata_json or "{}")
    assert metadata["workspace"] == "keypulse"
    assert metadata["text_dropped"] == "terminal_app"


def test_browser_url_denylist_drops_matching_host_before_persistence(monkeypatch):
    manager = CaptureManager(Config())
    inserts: list[RawEvent] = []

    monkeypatch.setattr(
        manager._policy,
        "apply",
        lambda _event: (_ for _ in ()).throw(AssertionError("policy should not run")),
    )
    monkeypatch.setattr(
        manager._aggregator,
        "process",
        lambda _event: (_ for _ in ()).throw(AssertionError("aggregator should not run")),
    )
    monkeypatch.setattr(
        "keypulse.capture.manager.insert_raw_event",
        lambda raw_event: inserts.append(raw_event),
    )

    manager._process_event(
        normalize_browser_tab_event(
            url="https://WWW.XIAOHONGSHU.COM/explore/123",
            title="Explore",
            browser_name="Safari",
            ts_start="2026-04-18T09:00:00+00:00",
        )
    )

    assert inserts == []


def test_browser_url_denylist_ignores_invalid_url_and_persists_event(monkeypatch):
    manager = CaptureManager(Config())
    captured: list[RawEvent] = []

    monkeypatch.setattr(manager._aggregator, "process", lambda _event: None)
    monkeypatch.setattr(manager._policy, "apply", lambda raw_event: raw_event)
    monkeypatch.setattr(
        "keypulse.capture.manager.insert_raw_event",
        lambda raw_event: captured.append(raw_event) or 1,
    )
    monkeypatch.setattr("keypulse.capture.manager.insert_search_doc", lambda _doc: 1)
    monkeypatch.setattr("keypulse.capture.manager.set_state", lambda *_args, **_kwargs: None)

    manager._process_event(
        normalize_browser_tab_event(
            url="http://[::1",
            title="Bad URL",
            browser_name="Safari",
            ts_start="2026-04-18T09:00:00+00:00",
        )
    )

    assert len(captured) == 1
    assert captured[0].source == "browser"
    assert captured[0].window_title == "Bad URL"


def test_metadata_json_parse_failure_is_preserved(monkeypatch):
    manager = CaptureManager(Config())
    captured: list[RawEvent] = []
    event = RawEvent(
        source="manual",
        event_type="manual_save",
        ts_start="2026-04-18T09:00:00+00:00",
        app_name="Notes",
        window_title="Draft",
        content_text="plain text",
        metadata_json="{bad json",
    )

    monkeypatch.setattr(manager._aggregator, "process", lambda _event: None)
    monkeypatch.setattr(manager._policy, "apply", lambda raw_event: raw_event)
    monkeypatch.setattr(
        "keypulse.capture.manager.insert_raw_event",
        lambda raw_event: captured.append(raw_event) or 1,
    )
    monkeypatch.setattr("keypulse.capture.manager.insert_search_doc", lambda _doc: 1)
    monkeypatch.setattr("keypulse.capture.manager.set_state", lambda *_args, **_kwargs: None)

    manager._process_event(event)

    assert captured
    assert captured[0].metadata_json == "{bad json"
