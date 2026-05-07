from __future__ import annotations

import json
from datetime import datetime, timezone

from keypulse.quality.entity_extractor import extract_named_entities
from keypulse.sources.sink import semantic_event_to_raw_event
from keypulse.sources.types import SemanticEvent


def _event(
    source: str,
    *,
    intent: str = "intent",
    artifact: str = "artifact",
    metadata: dict | None = None,
) -> SemanticEvent:
    return SemanticEvent(
        time=datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc),
        source=source,
        actor="user",
        intent=intent,
        artifact=artifact,
        raw_ref=f"{source}:row:1",
        privacy_tier="green",
        metadata=metadata or {},
    )


def _entities(raw_event_metadata_json: str | None) -> dict:
    payload = json.loads(raw_event_metadata_json or "{}")
    return payload.get("entities", {})


def test_git_log_event_maps_commit_hash_and_repo_path(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.sources.sink.extract_named_entities", lambda event: ["KeyPulse", "KeyPulse", "hud"])
    event = _event(
        "git_log",
        artifact="commit:abc1234",
        metadata={"full_hash": "abc1234def5678", "repo_path": "/tmp/work/keypulse"},
    )

    raw_event = semantic_event_to_raw_event(event)
    entities = _entities(raw_event.metadata_json)

    assert entities["commit_hash"] == "abc1234def5678"
    assert entities["file_paths"] == ["/tmp/work/keypulse"]
    assert entities["named_entities"] == ["KeyPulse", "hud"]


def test_claude_and_codex_map_session_id(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.sources.sink.extract_named_entities", lambda event: [])
    claude_event = _event(
        "claude_code",
        metadata={"session_id": "claude-s1", "project_dir": "/Users/a/Go/keypulse"},
    )
    codex_event = _event(
        "codex_cli",
        metadata={"session_id": "codex-s2"},
    )

    claude_entities = _entities(semantic_event_to_raw_event(claude_event).metadata_json)
    codex_entities = _entities(semantic_event_to_raw_event(codex_event).metadata_json)

    assert claude_entities["session_id"] == "claude-s1"
    assert claude_entities["file_paths"] == ["/Users/a/Go/keypulse"]
    assert codex_entities["session_id"] == "codex-s2"


def test_markdown_vault_maps_file_path(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.sources.sink.extract_named_entities", lambda event: [])
    event = _event("markdown_vault", artifact="Daily/2026-05-06.md")
    raw_event = semantic_event_to_raw_event(event)
    entities = _entities(raw_event.metadata_json)

    assert entities["file_paths"] == ["Daily/2026-05-06.md"]


def test_browser_source_entities_url_strips_query_but_keeps_original_url(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.sources.sink.extract_named_entities", lambda event: [])
    event = _event(
        "chrome_history",
        artifact="https://example.com/a/b?token=1#frag",
        metadata={"full_url": "https://example.com/a/b?token=1#frag"},
    )

    raw_event = semantic_event_to_raw_event(event)
    payload = json.loads(raw_event.metadata_json or "{}")
    entities = payload["entities"]

    assert entities["urls"] == ["https://example.com/a/b"]
    assert payload["url"] == "https://example.com/a/b?token=1#frag"


def test_safari_history_url_query_is_removed_in_entities(monkeypatch) -> None:
    monkeypatch.setattr("keypulse.sources.sink.extract_named_entities", lambda event: [])
    event = _event(
        "safari_history",
        artifact="https://apple.com/cn?x=1",
        metadata={"url": "https://apple.com/cn?x=1"},
    )

    raw_event = semantic_event_to_raw_event(event)
    entities = _entities(raw_event.metadata_json)
    assert entities["urls"] == ["https://apple.com/cn"]


def test_quality_entity_extractor_returns_deduped_named_entities() -> None:
    event = _event(
        "claude_code",
        intent="实现 timeline cache 并修复 sync bug",
        metadata={"session_id": "s-1"},
    )
    named_entities = extract_named_entities(event)

    assert "timeline" in named_entities
    assert "cache" in named_entities
    assert len(named_entities) == len(set(named_entities))
