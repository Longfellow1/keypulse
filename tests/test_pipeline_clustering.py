from __future__ import annotations

import json

from keypulse.pipeline.clustering import (
    build_evidence_graph,
    build_feature_index,
    connected_components,
    detect_merge_candidates,
)


def _event(
    event_id: str,
    ts_start: str,
    *,
    session_id: str | None = None,
    app_name: str = "Terminal",
    window_title: str = "main",
    content_text: str = "",
    file_paths: list[str] | None = None,
    urls: list[str] | None = None,
    named_entities: list[str] | None = None,
) -> dict[str, object]:
    entities: dict[str, object] = {}
    if session_id:
        entities["session_id"] = session_id
    if file_paths:
        entities["file_paths"] = file_paths
    if urls:
        entities["urls"] = urls
    if named_entities:
        entities["named_entities"] = named_entities
    return {
        "id": event_id,
        "ts_start": ts_start,
        "app_name": app_name,
        "window_title": window_title,
        "content_text": content_text,
        "metadata_json": json.dumps({"entities": entities}, ensure_ascii=False),
    }


def test_build_evidence_graph_h1_hits_even_over_30_minutes():
    events = [
        _event("e1", "2026-05-01T00:00:00+00:00", file_paths=["/repo/keypulse/pipeline.py"]),
        _event("e2", "2026-05-01T01:10:00+00:00", file_paths=["/repo/keypulse/pipeline.py"]),
    ]

    graph = build_evidence_graph(events)

    assert graph["e1"] == {"e2"}
    assert graph["e2"] == {"e1"}


def test_build_evidence_graph_h2_session_hits():
    events = [
        _event("e1", "2026-05-01T10:00:00+00:00", session_id="claude-s1"),
        _event("e2", "2026-05-01T10:50:00+00:00", session_id="claude-s1"),
    ]

    graph = build_evidence_graph(events)

    assert graph["e1"] == {"e2"}
    assert graph["e2"] == {"e1"}


def test_build_evidence_graph_h3_direct_hit_under_5_minutes():
    events = [
        _event("e1", "2026-05-01T11:00:00+00:00", app_name="A", window_title="w1"),
        _event("e2", "2026-05-01T11:03:00+00:00", app_name="B", window_title="w2"),
    ]

    graph = build_evidence_graph(events)

    assert graph["e1"] == {"e2"}
    assert graph["e2"] == {"e1"}


def test_build_evidence_graph_h3_boost_hit_between_5_and_30_minutes():
    events = [
        _event("e1", "2026-05-01T12:00:00+00:00", app_name="Codex", window_title="repo-a"),
        _event("e2", "2026-05-01T12:12:00+00:00", app_name="Codex", window_title="repo-a"),
    ]

    graph = build_evidence_graph(events)

    assert graph["e1"] == {"e2"}
    assert graph["e2"] == {"e1"}


def test_build_evidence_graph_does_not_link_without_h1_h2_boost():
    events = [
        _event("e1", "2026-05-01T13:00:00+00:00", app_name="A", window_title="a"),
        _event("e2", "2026-05-01T13:20:00+00:00", app_name="B", window_title="b"),
    ]

    graph = build_evidence_graph(events)

    assert graph["e1"] == set()
    assert graph["e2"] == set()


def test_build_evidence_graph_over_30_minutes_needs_strong_evidence():
    events = [
        _event("e1", "2026-05-01T14:00:00+00:00", app_name="Codex", window_title="same-window"),
        _event("e2", "2026-05-01T14:50:00+00:00", app_name="Codex", window_title="same-window"),
    ]

    graph = build_evidence_graph(events)

    assert graph["e1"] == set()
    assert graph["e2"] == set()


def test_connected_components_and_merge_candidates_across_components():
    events = [
        _event("e1", "2026-05-01T15:00:00+00:00", session_id="s1", content_text="keypulse daily orchestrator"),
        _event("e2", "2026-05-01T15:04:00+00:00", session_id="s1", content_text="keypulse topics update"),
        _event("e3", "2026-05-01T18:00:00+00:00", session_id="s2", content_text="weekly report design"),
        _event("e4", "2026-05-01T18:03:00+00:00", session_id="s2", content_text="weekly report keypulse"),
    ]

    graph = build_evidence_graph(events)
    components = connected_components(graph)
    assert len(components) == 2

    feature_index = build_feature_index(events)
    candidates = detect_merge_candidates(components, 0.1, feature_index=feature_index)

    assert len(candidates) == 1
