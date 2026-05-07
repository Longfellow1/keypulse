from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from keypulse.sources import registry
from keypulse.sources.cleaning.config import CleaningConfig
from keypulse.sources.sink import persist_semantic_event
from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent
from keypulse.store.db import close, get_conn, init_db


class _SixSourceFixture(DataSource):
    name = "fixture"
    privacy_tier = "green"
    liveness = "always"

    def discover(self) -> list[DataSourceInstance]:
        return [DataSourceInstance(plugin=self.name, locator="/tmp/six", label="six")]

    def read(self, instance: DataSourceInstance, since: datetime, until: datetime):
        base = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
        events = [
            SemanticEvent(
                time=base,
                source="git_log",
                actor="alice",
                intent="commit",
                artifact="commit:abc1234",
                raw_ref="git:keypulse:abc1234",
                privacy_tier="green",
                metadata={"full_hash": "abc1234def", "repo_path": "/tmp/repo/keypulse"},
            ),
            SemanticEvent(
                time=base + timedelta(minutes=1),
                source="claude_code",
                actor="user",
                intent="fix test",
                artifact="claude:session:s1",
                raw_ref="claude:r1",
                privacy_tier="green",
                metadata={"session_id": "claude-s1", "project_dir": "/tmp/repo/keypulse"},
            ),
            SemanticEvent(
                time=base + timedelta(minutes=2),
                source="codex_cli",
                actor="user",
                intent="update sink",
                artifact="codex:session:c1",
                raw_ref="codex:r1",
                privacy_tier="green",
                metadata={"session_id": "codex-s1"},
            ),
            SemanticEvent(
                time=base + timedelta(minutes=3),
                source="markdown_vault",
                actor="user",
                intent="Daily",
                artifact="Daily/2026-05-06.md",
                raw_ref="markdown:r1",
                privacy_tier="green",
                metadata={"vault_name": "Knowledge"},
            ),
            SemanticEvent(
                time=base + timedelta(minutes=4),
                source="chrome_history",
                actor="user",
                intent="Example",
                artifact="https://example.com/docs?a=1",
                raw_ref="chrome:r1",
                privacy_tier="green",
                metadata={"full_url": "https://example.com/docs?a=1"},
            ),
            SemanticEvent(
                time=base + timedelta(minutes=5),
                source="safari_history",
                actor="user",
                intent="Apple",
                artifact="https://apple.com/cn?x=2",
                raw_ref="safari:r1",
                privacy_tier="green",
                metadata={"full_url": "https://apple.com/cn?x=2"},
            ),
        ]
        for event in events:
            if since <= event.time <= until:
                yield event


def test_read_all_persists_source_events_to_raw_events(tmp_path, monkeypatch) -> None:
    close()
    init_db(tmp_path / "keypulse.db")
    monkeypatch.setattr(registry, "_PLUGINS", {"fixture": _SixSourceFixture()})
    monkeypatch.setattr(
        registry,
        "load_cleaning_config",
        lambda: CleaningConfig(privacy_max_tier="red", dedup_time_window_minutes=0),
    )
    monkeypatch.setattr("keypulse.sources.sink.extract_named_entities", lambda event: [])
    since = datetime(2026, 5, 6, 0, 0, tzinfo=timezone.utc)
    until = datetime(2026, 5, 6, 23, 59, tzinfo=timezone.utc)

    events = list(registry.read_all(since, until, source="fixture"))
    assert len(events) == 6

    rows = get_conn().execute(
        "SELECT source, metadata_json FROM raw_events WHERE source IN (?,?,?,?,?,?) ORDER BY ts_start ASC",
        ("git_log", "claude_code", "codex_cli", "markdown_vault", "chrome_history", "safari_history"),
    ).fetchall()
    assert len(rows) == 6

    parsed = {row["source"]: json.loads(row["metadata_json"] or "{}") for row in rows}
    assert parsed["git_log"]["entities"]["commit_hash"] == "abc1234def"
    assert parsed["claude_code"]["entities"]["session_id"] == "claude-s1"
    assert parsed["codex_cli"]["entities"]["session_id"] == "codex-s1"
    assert parsed["markdown_vault"]["entities"]["file_paths"] == ["Daily/2026-05-06.md"]
    assert parsed["chrome_history"]["entities"]["urls"] == ["https://example.com/docs"]
    assert parsed["safari_history"]["entities"]["urls"] == ["https://apple.com/cn"]

    close()


def test_named_entities_are_backfilled_to_metadata(tmp_path, monkeypatch) -> None:
    close()
    init_db(tmp_path / "keypulse.db")
    monkeypatch.setattr("keypulse.sources.sink.extract_named_entities", lambda event: ["timeline", "timeline", "cache"])
    event = SemanticEvent(
        time=datetime(2026, 5, 6, 11, 0, tzinfo=timezone.utc),
        source="claude_code",
        actor="user",
        intent="实现 timeline cache 并修复 sync bug",
        artifact="claude:session:entity-s1",
        raw_ref="claude:entity:1",
        privacy_tier="green",
        metadata={"session_id": "entity-s1", "project_dir": "/tmp/repo/keypulse"},
    )
    row_id = persist_semantic_event(event, async_named_entities=False)
    assert row_id > 0

    row = get_conn().execute(
        "SELECT metadata_json FROM raw_events WHERE source = 'claude_code' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    payload = json.loads(row["metadata_json"] or "{}")
    named_entities = payload.get("entities", {}).get("named_entities", [])
    assert named_entities == ["timeline", "cache"]

    close()
