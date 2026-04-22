from __future__ import annotations

from keypulse.capture.aggregator import Aggregator
from keypulse.capture.normalizer import normalize_window_event


def test_aggregator_tracks_window_title_changes_within_current_session(monkeypatch):
    aggregator = Aggregator()
    monkeypatch.setattr("keypulse.capture.aggregator.upsert_session", lambda _session: None)

    focus = normalize_window_event(
        event_type="window_focus",
        app_name="Code",
        window_title="main.py",
        process_name="com.microsoft.VSCode",
        ts_start="2026-04-19T09:00:00+00:00",
    )
    aggregator.process(focus)

    title_change = normalize_window_event(
        event_type="window_title_changed",
        app_name="Code",
        window_title="other.py",
        process_name="com.microsoft.VSCode",
        ts_start="2026-04-19T09:00:05+00:00",
    )
    session = aggregator.process(title_change)

    assert session is not None
    assert session.app_name == "Code"
    assert session.primary_window_title == "other.py"
    assert session.event_count == 2


def test_aggregator_starts_session_from_window_title_change(monkeypatch):
    aggregator = Aggregator()
    monkeypatch.setattr("keypulse.capture.aggregator.upsert_session", lambda _session: None)

    title_change = normalize_window_event(
        event_type="window_title_changed",
        app_name="Code",
        window_title="other.py",
        process_name="com.microsoft.VSCode",
        ts_start="2026-04-19T09:00:05+00:00",
    )
    session = aggregator.process(title_change)

    assert session is not None
    assert session.app_name == "Code"
    assert session.primary_window_title == "other.py"
