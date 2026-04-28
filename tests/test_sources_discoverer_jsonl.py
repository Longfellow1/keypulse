from __future__ import annotations

import json
from pathlib import Path

from keypulse.sources.discoverers.jsonl import discover_jsonl_candidates


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_discover_jsonl_candidates_scores_small_files(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    high_file = home / ".codex" / "history.jsonl"
    medium_file = home / ".local" / "state" / "records.jsonl"
    ignored_file = home / ".cache" / "plain.jsonl"
    excluded_file = home / ".data" / "node_modules" / "skip.jsonl"

    _write_jsonl(
        high_file,
        [
            {
                "role": "user",
                "message": "hello",
                "content": "world",
                "session_id": "s1",
                "prompt": "p1",
            }
        ],
    )
    _write_jsonl(medium_file, [{"user": "alice", "content": "note"}])
    _write_jsonl(ignored_file, [{"foo": "bar"}])
    _write_jsonl(excluded_file, [{"role": "assistant", "content": "skip"}])

    candidates = discover_jsonl_candidates(exclude_paths=set())

    by_path = {candidate.path: candidate for candidate in candidates}
    assert str(high_file.resolve()) in by_path
    assert str(medium_file.resolve()) in by_path
    assert str(ignored_file.resolve()) not in by_path
    assert str(excluded_file.resolve()) not in by_path

    high = by_path[str(high_file.resolve())]
    assert high.confidence == "high"
    assert set(high.hint_tables) >= {"role", "message", "content", "session_id", "prompt"}

    medium = by_path[str(medium_file.resolve())]
    assert medium.confidence == "medium"
    assert set(medium.hint_tables) == {"user", "content"}


def test_discover_jsonl_candidates_marks_large_file_low_confidence(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    large_file = home / ".codex" / "huge.jsonl"
    large_file.parent.mkdir(parents=True, exist_ok=True)
    with large_file.open("wb") as handle:
        handle.truncate(51 * 1024 * 1024)

    candidates = discover_jsonl_candidates(exclude_paths=set())

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.path == str(large_file.resolve())
    assert candidate.confidence == "low"
    assert candidate.hint_tables == []


def test_discover_jsonl_candidates_respects_excluded_paths(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    file_path = home / ".codex" / "history.jsonl"
    _write_jsonl(file_path, [{"role": "user", "content": "exclude me"}])

    candidates = discover_jsonl_candidates(exclude_paths={str((home / ".codex").resolve())})

    assert candidates == []


def test_discover_jsonl_candidates_honors_depth_limit(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)

    deep_file = home / ".foo" / "a" / "b" / "c" / "d" / "e" / "too-deep.jsonl"
    _write_jsonl(deep_file, [{"role": "user", "message": "x", "content": "y"}])

    candidates = discover_jsonl_candidates(exclude_paths=set())

    assert candidates == []
