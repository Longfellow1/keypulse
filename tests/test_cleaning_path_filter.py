from __future__ import annotations

from pathlib import Path

from penpal.sources.cleaning.path_filter import is_excluded_path
from penpal.sources.cleaning import path_filter


def test_path_filter_excludes_backup_and_cache() -> None:
    assert is_excluded_path(Path('/tmp/.claude-backup-20260323/foo.jsonl'))[0]
    assert is_excluded_path(Path('/tmp/node_modules/pkg/index.js'))[0]
    assert is_excluded_path(Path('/tmp/Library/Caches/x.db'))[0]
    assert is_excluded_path(Path.home() / 'Library' / 'Containers' / 'com.foo' / 'state.db')[0]
    assert is_excluded_path(Path.home() / 'Library' / 'Application Support' / 'Foo' / 'config.json')[0]


def test_path_filter_allows_normal_paths() -> None:
    excluded, reason = is_excluded_path(Path('/tmp/workspace/events/history.jsonl'))
    assert excluded is False
    assert reason == ''


def test_path_filter_expands_current_user_home_consistently(monkeypatch, tmp_path) -> None:
    path_home = tmp_path / 'path-home'
    expanduser_home = tmp_path / 'expanduser-home'
    monkeypatch.setenv('HOME', str(expanduser_home))
    monkeypatch.setattr(path_filter.Path, 'home', lambda: path_home)

    target = path_home / 'Library' / 'Containers' / 'com.foo' / 'state.db'
    assert is_excluded_path(target)[0]
