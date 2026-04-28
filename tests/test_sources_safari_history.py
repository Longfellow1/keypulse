from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from keypulse.sources.plugins.safari_history import SafariHistorySource
from keypulse.sources.types import DataSourceInstance


CF_EPOCH_OFFSET_S = 978_307_200


def _to_cf_seconds(unix_seconds: float) -> float:
    return unix_seconds - CF_EPOCH_OFFSET_S


def _make_safari_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
        conn.execute(
            "CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER, title TEXT, visit_time REAL)"
        )
        conn.execute("INSERT INTO history_items(id, url) VALUES (1, 'https://apple.com/path?q=1#x')")
        conn.execute("INSERT INTO history_items(id, url) VALUES (2, 'https://example.org')")
        conn.execute(
            "INSERT INTO history_visits(id, history_item, title, visit_time) VALUES (20, 1, 'Apple', ?)",
            (_to_cf_seconds(datetime(2026, 4, 28, 2, 0, tzinfo=timezone.utc).timestamp()),),
        )
        conn.execute(
            "INSERT INTO history_visits(id, history_item, title, visit_time) VALUES (21, 2, 'Old', ?)",
            (_to_cf_seconds(datetime(2026, 4, 26, 2, 0, tzinfo=timezone.utc).timestamp()),),
        )
        conn.commit()
    finally:
        conn.close()


def test_safari_discover(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    history = home / "Library" / "Safari" / "History.db"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_bytes(b"sqlite placeholder")

    source = SafariHistorySource()
    instances = source.discover()

    assert len(instances) == 1
    assert instances[0].locator == str(history.resolve())


def test_safari_read_uses_temp_copy_and_maps_event(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    history = home / "Library" / "Safari" / "History.db"
    _make_safari_db(history)

    source = SafariHistorySource()
    instance = DataSourceInstance(plugin="safari_history", locator=str(history.resolve()), label="Safari")

    original_copy2 = __import__("shutil").copy2
    copied: list[tuple[str, str]] = []

    def tracking_copy2(src, dst, *args, **kwargs):
        copied.append((str(src), str(dst)))
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr("keypulse.sources.plugins.safari_history.shutil.copy2", tracking_copy2)

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
    assert event.source == "safari_history"
    assert event.intent == "Apple"
    assert event.artifact == "https://apple.com/path"
    assert event.raw_ref == "safari:visit:20"
    assert event.metadata["full_url"] == "https://apple.com/path?q=1#x"


def test_safari_discover_returns_empty_when_missing(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    source = SafariHistorySource()

    assert source.discover() == []
