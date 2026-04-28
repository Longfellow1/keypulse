from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from keypulse.sources.plugins.chrome_history import ChromeHistorySource
from keypulse.sources.types import DataSourceInstance


WEBKIT_EPOCH_OFFSET_US = 11_644_473_600 * 1_000_000


def _to_webkit_us(unix_seconds: float) -> int:
    return int(unix_seconds * 1_000_000) + WEBKIT_EPOCH_OFFSET_US


def _make_history_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
        conn.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
        conn.execute("INSERT INTO urls(id, url, title) VALUES (1, 'https://example.com/a?token=abc#frag', 'Example A')")
        conn.execute("INSERT INTO urls(id, url, title) VALUES (2, 'https://example.com/b', 'Example B')")
        conn.execute(
            "INSERT INTO visits(id, url, visit_time) VALUES (10, 1, ?)",
            (_to_webkit_us(datetime(2026, 4, 28, 1, 0, tzinfo=timezone.utc).timestamp()),),
        )
        conn.execute(
            "INSERT INTO visits(id, url, visit_time) VALUES (11, 2, ?)",
            (_to_webkit_us(datetime(2026, 4, 27, 1, 0, tzinfo=timezone.utc).timestamp()),),
        )
        conn.commit()
    finally:
        conn.close()


def test_chrome_discover_profiles(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    default_history = home / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History"
    profile_history = home / "Library" / "Application Support" / "Google" / "Chrome" / "Profile 1" / "History"
    default_history.parent.mkdir(parents=True, exist_ok=True)
    profile_history.parent.mkdir(parents=True, exist_ok=True)
    default_history.write_bytes(b"sqlite placeholder")
    profile_history.write_bytes(b"sqlite placeholder")

    source = ChromeHistorySource()
    instances = source.discover()

    assert {instance.metadata["profile"] for instance in instances} == {"Default", "Profile 1"}


def test_chrome_read_uses_temp_copy_and_maps_event(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    history = home / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "History"
    _make_history_db(history)

    source = ChromeHistorySource()
    instance = DataSourceInstance(
        plugin="chrome_history",
        locator=str(history.resolve()),
        label="Default",
        metadata={"profile": "Default"},
    )

    original_copy2 = __import__("shutil").copy2
    copied: list[tuple[str, str]] = []

    def tracking_copy2(src, dst, *args, **kwargs):
        copied.append((str(src), str(dst)))
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr("keypulse.sources.plugins.chrome_history.shutil.copy2", tracking_copy2)

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert copied
    assert len(events) == 1
    event = events[0]
    assert event.source == "chrome_history"
    assert event.intent == "Example A"
    assert event.artifact == "https://example.com/a"
    assert event.raw_ref == "chrome:visit:10"
    assert event.metadata["profile"] == "Default"
    assert event.metadata["full_url"].startswith("https://example.com/a")
    assert "[REDACTED]" in event.metadata["full_url"]


def test_chrome_discover_returns_empty_when_missing(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    source = ChromeHistorySource()

    assert source.discover() == []
