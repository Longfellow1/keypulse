from __future__ import annotations

from pathlib import Path

from keypulse.sources.cleaning.path_filter import is_excluded_path


def test_path_filter_excludes_backup_and_cache() -> None:
    assert is_excluded_path(Path('/tmp/.claude-backup-20260323/foo.jsonl'))[0]
    assert is_excluded_path(Path('/tmp/node_modules/pkg/index.js'))[0]
    assert is_excluded_path(Path('/tmp/Library/Caches/x.db'))[0]


def test_path_filter_allows_normal_paths() -> None:
    excluded, reason = is_excluded_path(Path('/tmp/workspace/events/history.jsonl'))
    assert excluded is False
    assert reason == ''
