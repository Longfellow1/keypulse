from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from keypulse.sources.plugins.zsh_history import ZshHistorySource


def test_zsh_discover_and_read_extended_lines(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    history = home / ".zsh_history"
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("wb") as handle:
        handle.write(b": 1774855914:5;git status\n")
        handle.write(b"plain command should be ignored\n")
        handle.write(b": 1774856914:12;echo bad-\xff-byte\n")

    source = ZshHistorySource()
    instances = source.discover()

    assert len(instances) == 1
    assert instances[0].locator == str(history.resolve())

    events = list(
        source.read(
            instances[0],
            datetime.fromtimestamp(1774855900, tz=timezone.utc),
            datetime.fromtimestamp(1774856000, tz=timezone.utc),
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event.source == "zsh_history"
    assert event.actor == "user"
    assert event.intent == "git status"
    assert event.artifact == "shell:zsh"
    assert event.raw_ref == "zsh:line:1"
    assert event.metadata == {"elapsed_seconds": 5}


def test_zsh_discover_returns_empty_when_missing(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    source = ZshHistorySource()

    assert source.discover() == []
