from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from keypulse.sources.plugins.claude_code import ClaudeCodeSource
from keypulse.sources.types import DataSourceInstance


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_discover_claude_projects(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    project_dir = home / ".claude" / "projects" / "-Users-someone-Work-keypulse"
    _write_jsonl(project_dir / "session-a.jsonl", [{"type": "user", "timestamp": "2026-04-28T01:00:00Z"}])
    _write_jsonl(project_dir / "session-b.jsonl", [{"type": "assistant", "timestamp": "2026-04-28T02:00:00Z"}])

    source = ClaudeCodeSource()
    instances = source.discover()

    assert len(instances) == 1
    instance = instances[0]
    assert instance.plugin == "claude_code"
    assert instance.locator == str(project_dir.resolve())
    assert instance.label == "keypulse"
    assert instance.metadata["project_path"] == "/Users/someone/Work/keypulse"
    assert instance.metadata["session_count"] == 2


def test_read_claude_jsonl_handles_dict_and_stringified_message(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    project_dir = home / ".claude" / "projects" / "-Users-someone-Work-keypulse"
    session_file = project_dir / "abc-session.jsonl"
    rows = [
        {
            "type": "user",
            "uuid": "u-1",
            "parentUuid": None,
            "timestamp": "2026-04-28T01:00:00Z",
            "message": {"role": "user", "content": "run tests now"},
        },
        {
            "type": "assistant",
            "uuid": "a-1",
            "parentUuid": "u-1",
            "timestamp": "2026-04-28T01:10:00Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "tests passed with fixes"}],
            },
        },
        {
            "type": "user",
            "uuid": "u-2",
            "parentUuid": "a-1",
            "timestamp": "2026-04-28T01:20:00Z",
            "message": "{'role': 'user', 'content': 'string payload'}",
        },
        {
            "type": "assistant",
            "uuid": "a-old",
            "parentUuid": "u-0",
            "timestamp": "2026-04-27T23:59:59Z",
            "message": {"role": "assistant", "content": "out of range"},
        },
        {
            "type": "tool_result",
            "timestamp": "2026-04-28T01:30:00Z",
            "message": {"content": "skip"},
        },
    ]
    _write_jsonl(session_file, rows)

    instance = DataSourceInstance(plugin="claude_code", locator=str(project_dir.resolve()), label="keypulse")
    source = ClaudeCodeSource()

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 30, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 1, 30, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 3
    assert [event.actor for event in events] == ["user", "assistant", "user"]
    assert events[0].intent == "run tests now"
    assert events[1].intent == "tests passed with fixes"
    assert events[2].intent == "string payload"
    assert events[0].artifact == "claude:session:abc-session:msg:u-1"
    assert events[1].metadata["parent_uuid"] == "u-1"
    assert events[2].raw_ref.endswith(":3")


def test_discover_claude_projects_returns_empty_when_missing(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    source = ClaudeCodeSource()

    assert source.discover() == []
