from __future__ import annotations

import sqlite3
from pathlib import Path

from keypulse.sources.discoverers.sqlite import discover_sqlite_candidates


def _make_db(path: Path, table_names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        for name in table_names:
            conn.execute(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY)')
        conn.commit()
    finally:
        conn.close()


def test_discover_sqlite_candidates_confidence_and_hints(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    high_db = home / "Library" / "Application Support" / "Cursor" / "state.vscdb"
    medium_db = home / "Library" / "Application Support" / "Warp" / "warp.sqlite"
    ignored_db = home / "Library" / "Application Support" / "Other" / "misc.db"
    fake_db = home / "Library" / "Application Support" / "Bad" / "not-sqlite.db"

    _make_db(high_db, ["messages", "chats", "events", "users"])
    _make_db(medium_db, ["commands", "metadata"])
    _make_db(ignored_db, ["settings", "flags"])
    fake_db.parent.mkdir(parents=True, exist_ok=True)
    fake_db.write_text("not sqlite", encoding="utf-8")

    candidates = discover_sqlite_candidates(exclude_paths=set())

    by_path = {candidate.path: candidate for candidate in candidates}
    assert str(high_db.resolve()) in by_path
    assert str(medium_db.resolve()) in by_path
    assert str(ignored_db.resolve()) not in by_path
    assert str(fake_db.resolve()) not in by_path

    high = by_path[str(high_db.resolve())]
    assert high.discoverer == "sqlite"
    assert high.confidence == "high"
    assert set(high.hint_tables) == {"messages", "chats", "events"}
    assert high.schema_signature == "chats,events,messages,users"

    medium = by_path[str(medium_db.resolve())]
    assert medium.confidence == "medium"
    assert medium.hint_tables == ["commands"]


def test_discover_sqlite_candidates_respects_excluded_paths(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    db_path = home / "Library" / "Application Support" / "App" / "history.db"
    _make_db(db_path, ["history", "metadata"])

    candidates = discover_sqlite_candidates(exclude_paths={str(db_path.resolve())})

    assert candidates == []


def test_discover_sqlite_candidates_honors_depth_limit(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    deep_db = (
        home
        / "Library"
        / "Application Support"
        / "a"
        / "b"
        / "c"
        / "d"
        / "e"
        / "f"
        / "too-deep.db"
    )
    _make_db(deep_db, ["history"])

    candidates = discover_sqlite_candidates(exclude_paths=set())

    assert candidates == []
