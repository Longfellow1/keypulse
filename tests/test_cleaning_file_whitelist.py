from __future__ import annotations

from pathlib import Path

from keypulse.sources.cleaning.file_whitelist import is_blocked_sqlite


def test_sqlite_whitelist_blocks_sensitive_files() -> None:
    assert is_blocked_sqlite(Path('/tmp/Cookies'))[0]
    assert is_blocked_sqlite(Path('/tmp/Login Data'))[0]
    assert is_blocked_sqlite(Path('/tmp/Web Data'))[0]
    assert is_blocked_sqlite(Path('/tmp/History-journal'))[0]
    assert is_blocked_sqlite(Path('/tmp/a.sqlite-wal'))[0]


def test_sqlite_whitelist_photos_special_case() -> None:
    assert is_blocked_sqlite(Path('/tmp/Containers/A/Photos.sqlite'))[0]
    allowed, _ = is_blocked_sqlite(Path('/Users/u/Pictures/Photos Library.photoslibrary/database/Photos.sqlite'))
    assert allowed is False
