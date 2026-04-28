from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from keypulse.sources.plugins.codex_cli import CodexCliSource


def test_codex_cli_discover_and_read(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    history = home / ".codex" / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        "\n".join(
            [
                json.dumps({"session_id": "s-1", "ts": 1774855914, "text": "first prompt"}),
                "{bad json}",
                json.dumps({"session_id": "s-2", "ts": 1774856914, "text": "second prompt"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    source = CodexCliSource()
    instances = source.discover()

    assert len(instances) == 1
    instance = instances[0]
    assert instance.plugin == "codex_cli"
    assert instance.locator == str(history.resolve())

    events = list(
        source.read(
            instance,
            datetime.fromtimestamp(1774855800, tz=timezone.utc),
            datetime.fromtimestamp(1774856000, tz=timezone.utc),
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event.source == "codex_cli"
    assert event.actor == "user"
    assert event.intent == "first prompt"
    assert event.artifact == "codex:session:s-1"
    assert event.raw_ref == "codex:history:1"
    assert event.metadata == {"session_id": "s-1"}


def test_codex_cli_discover_returns_empty_when_missing(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    source = CodexCliSource()

    assert source.discover() == []
