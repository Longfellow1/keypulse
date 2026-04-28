from __future__ import annotations

import json
from pathlib import Path

from keypulse.sources.discoverers.json_files import discover_json_files_candidates


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_discover_json_files_candidates_uses_field_categories(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    high_file = home / "Library" / "Application Support" / "Cursor" / "session.json"
    medium_file = home / "Library" / "Application Support" / "Codex" / "note.json"
    ignored_file = home / "Library" / "Application Support" / "App" / "plain.json"
    excluded_file = home / "Library" / "Application Support" / "App" / "cache-data.json"

    _write_json(high_file, {"role": "user", "message": "x", "url": "https://x", "path": "/tmp/a"})
    _write_json(medium_file, {"message": "x", "path": "/tmp/a"})
    _write_json(ignored_file, {"message": "x"})
    _write_json(excluded_file, {"role": "user", "message": "skip"})

    candidates = discover_json_files_candidates(exclude_paths=set())
    by_path = {candidate.path: candidate for candidate in candidates}

    assert str(high_file.resolve()) in by_path
    assert str(medium_file.resolve()) in by_path
    assert str(ignored_file.resolve()) not in by_path
    assert str(excluded_file.resolve()) not in by_path

    assert by_path[str(high_file.resolve())].confidence == "high"
    assert set(by_path[str(high_file.resolve())].hint_fields) >= {"role", "message", "url", "path"}
    assert by_path[str(medium_file.resolve())].confidence == "medium"


def test_discover_json_files_candidates_skips_large_or_invalid(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    large_file = home / "Library" / "Application Support" / "X" / "big.json"
    large_file.parent.mkdir(parents=True, exist_ok=True)
    with large_file.open("wb") as handle:
        handle.truncate(11 * 1024 * 1024)

    bad_file = home / "Library" / "Application Support" / "X" / "bad.json"
    bad_file.write_text("{not-json", encoding="utf-8")

    candidates = discover_json_files_candidates(exclude_paths=set())

    assert candidates == []


def test_discover_json_files_candidates_respects_excluded_paths(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    json_file = home / "Library" / "Application Support" / "Cursor" / "session.json"
    _write_json(json_file, {"role": "user", "message": "x", "url": "https://x"})

    candidates = discover_json_files_candidates(exclude_paths={str((home / "Library").resolve())})
    assert candidates == []
