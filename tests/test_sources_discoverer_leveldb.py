from __future__ import annotations

from pathlib import Path

from keypulse.sources.discoverers.leveldb import discover_leveldb_candidates


def _make_leveldb_instance(path: Path, *, ldb_files: int = 2) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "MANIFEST-000001").write_text("manifest", encoding="utf-8")
    (path / "CURRENT").write_text("000001", encoding="utf-8")
    for idx in range(ldb_files):
        (path / f"{idx:06d}.ldb").write_bytes(b"x" * 1024)


def test_discover_leveldb_candidates_detects_instances(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    cursor_dir = home / "Library" / "Application Support" / "Cursor" / "Session Storage"
    unknown_dir = home / "Library" / "Application Support" / "SomeApp" / "Local Storage" / "leveldb"
    invalid_dir = home / "Library" / "Application Support" / "Bad" / "Session Storage"

    _make_leveldb_instance(cursor_dir)
    _make_leveldb_instance(unknown_dir)
    invalid_dir.mkdir(parents=True, exist_ok=True)
    (invalid_dir / "MANIFEST-000001").write_text("manifest", encoding="utf-8")
    (invalid_dir / "CURRENT").write_text("000001", encoding="utf-8")
    (invalid_dir / "000001.ldb").write_bytes(b"x")

    candidates = discover_leveldb_candidates(exclude_paths=set())
    by_path = {candidate.path: candidate for candidate in candidates}

    assert str(cursor_dir.resolve()) in by_path
    assert str(unknown_dir.resolve()) in by_path
    assert str(invalid_dir.resolve()) not in by_path

    assert by_path[str(cursor_dir.resolve())].confidence == "high"
    assert by_path[str(cursor_dir.resolve())].app_hint == "Cursor"
    assert by_path[str(unknown_dir.resolve())].confidence == "medium"


def test_discover_leveldb_candidates_respects_excluded_paths(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    leveldb_dir = home / "Library" / "Application Support" / "Codex" / "Local Storage" / "leveldb"
    _make_leveldb_instance(leveldb_dir)

    candidates = discover_leveldb_candidates(exclude_paths={str(leveldb_dir.resolve())})

    assert candidates == []


def test_discover_leveldb_candidates_honors_depth_limit(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    deep_dir = (
        home
        / "Library"
        / "Application Support"
        / "a"
        / "b"
        / "c"
        / "d"
        / "e"
        / "f"
        / "Local Storage"
        / "leveldb"
    )
    _make_leveldb_instance(deep_dir)

    candidates = discover_leveldb_candidates(exclude_paths=set())
    assert candidates == []
