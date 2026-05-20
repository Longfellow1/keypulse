#!/usr/bin/env python3
"""One-off migration: ~/.keypulse/weekly-anchor.json -> ~/.keypulse/anchors.json.

Safety:
- Never deletes legacy file.
- No-op when target already exists.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from keypulse.pipeline.weekly_topic_anchor import migrate_legacy_weekly_anchor_file


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate weekly-anchor.json to anchors.json")
    parser.add_argument(
        "--legacy",
        default=str(Path.home() / ".keypulse" / "weekly-anchor.json"),
        help="Legacy weekly anchor JSON path",
    )
    parser.add_argument(
        "--target",
        default=str(Path.home() / ".keypulse" / "anchors.json"),
        help="Target anchors JSON path",
    )
    parser.add_argument(
        "--week",
        default="",
        help="Optional fallback week label when legacy payload has no week",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    legacy = Path(args.legacy).expanduser()
    target = Path(args.target).expanduser()
    migrated = migrate_legacy_weekly_anchor_file(
        legacy_path=legacy,
        target_path=target,
        week_str=str(args.week or "").strip(),
    )
    if migrated:
        print(f"migrate_anchor_model=ok legacy={legacy} target={target}")
        print("legacy_kept=true")
        return 0
    print(f"migrate_anchor_model=skip legacy={legacy} target={target}")
    print("reason=target_exists_or_legacy_missing_or_payload_invalid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
