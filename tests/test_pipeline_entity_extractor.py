from __future__ import annotations

from datetime import datetime, timezone

from keypulse.pipeline.entity_extractor import extract
from keypulse.sources.types import SemanticEvent


def _event(intent: str = "", artifact: str = "", raw_ref: str = "", metadata: dict | None = None) -> SemanticEvent:
    return SemanticEvent(
        time=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        source="git_log",
        actor="Harland",
        intent=intent,
        artifact=artifact,
        raw_ref=raw_ref,
        privacy_tier="green",
        metadata=metadata or {},
    )


def test_extract_commit_from_artifact_prefers_short_hash() -> None:
    entities = extract(_event(artifact="commit:11a3a9b9c0f1"))
    commits = [e for e in entities if e.kind == "commit"]
    assert commits
    assert commits[0].value == "11a3a9b"


def test_extract_file_path_url_issue_and_session() -> None:
    event = _event(
        intent="修复 timeline pipeline #123 并看 https://example.com/docs/api?foo=1",
        artifact="updated keypulse/pipeline/things.py",
        raw_ref="JIRA-42",
        metadata={"session_id": "sess-abc", "repo_path": "/tmp/work/keypulse"},
    )
    entities = extract(event)

    kinds = {(entity.kind, entity.value) for entity in entities}
    assert ("file", "keypulse/pipeline/things.py") in kinds
    assert ("url", "example.com/docs/api") in kinds
    assert ("issue_pr", "#123") in kinds
    assert ("issue_pr", "JIRA-42") in kinds
    assert ("session", "sess-abc") in kinds
    assert ("project", "keypulse") in kinds


def test_extract_project_from_project_dir_and_raw_ref() -> None:
    entities = extract(
        _event(
            raw_ref="/Users/alice/work/rocket",
            metadata={"project_dir": "/Users/alice/work/rocket/sub"},
        )
    )
    project_values = [entity.value for entity in entities if entity.kind == "project"]
    assert "sub" in project_values
    assert "rocket" in project_values


def test_extract_intent_keyword_combo() -> None:
    entities = extract(_event(intent="实现 timeline cache 并修复 sync bug"))
    project_values = [entity.value for entity in entities if entity.kind == "project"]
    assert "timeline" in project_values
    assert "cache" in project_values
    assert "sync" in project_values
    assert "bug" in project_values


def test_extract_ignores_short_intent_tokens() -> None:
    entities = extract(_event(intent="修复 ux 与 db"))
    project_values = [entity.value for entity in entities if entity.kind == "project"]
    assert "ux" not in project_values
    assert "db" not in project_values


def test_extract_returns_empty_when_no_signal() -> None:
    entities = extract(_event(intent="", artifact="", raw_ref="", metadata={}))
    assert entities == []


def test_extract_commit_hash_requires_hex_letter() -> None:
    entities = extract(_event(intent="1777220651 1234567 11a3a9b bebc1b6"))
    commits = [e.value for e in entities if e.kind == "commit"]
    assert "11a3a9b" in commits
    assert "bebc1b6" in commits
    assert "1777220651" not in commits
    assert "1234567" not in commits
