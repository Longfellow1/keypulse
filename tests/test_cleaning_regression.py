from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from keypulse.pipeline.entity_extractor import extract
from keypulse.sources.cleaning.dedup import dedup_events
from keypulse.sources.cleaning.path_filter import is_excluded_path
from keypulse.sources.types import SemanticEvent


def test_regression_backup_paths_removed() -> None:
    for idx in range(21):
        excluded, _ = is_excluded_path(Path(f'/Users/u/.claude-backup-20260323/data-{idx}.jsonl'))
        assert excluded


def test_regression_qqmail_deduped_and_numeric_not_commit() -> None:
    events = [
        SemanticEvent(
            time=datetime(2026, 4, 28, 10, i, tzinfo=timezone.utc),
            source='chrome_history',
            actor='u',
            intent='wx.mail.qq.com/home/index',
            artifact='https://wx.mail.qq.com/home/index?sid=abc',
            raw_ref=f'chrome:visit:{i}',
            privacy_tier='green',
            metadata={},
        )
        for i in range(5)
    ]
    deduped = dedup_events(events, time_window_minutes=10)
    assert len(deduped) == 1
    extracted = extract(
        SemanticEvent(
            time=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
            source='claude_code',
            actor='u',
            intent='1777220651 and 11a3a9b',
            artifact='https://www.xiaohongshu.com/discovery/item/1777220651',
            raw_ref='r',
            privacy_tier='green',
            metadata={},
        )
    )
    commits = [item.value for item in extracted if item.kind == 'commit']
    assert '11a3a9b' in commits
    assert '1777220651' not in commits
