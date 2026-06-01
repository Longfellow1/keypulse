"""一次性清洗 ~/.keypulse/anchors.json 里 display 字段的「完成XX」前缀。

2026-06-01 事故复盘后的彻底收尾。L0_anchor.v1 prompt 旧 few-shot 误导
LLM 给每个 anchor display 套 `[项目]-[完成时动词][宾语]` 格式，导致
30+ 条 stale anchor 的 display 都是「KeyPulse-完成XX」「CorpusFlow-完成XX」
等。渲染层 override (commit 80a4285) 已治住当天 daily MD 显示，但 anchor
note 文件名 / aliases / weekly 输入仍带「完成」字眼。本脚本做一次性清洗。

策略：
- 正则匹配「项目-完成时动词宾语」格式，重写为「项目 宾语」
- 完成时动词候选：完成 / 完成了 / 打通 / 打通了 / 上线 / 上线了 / 收官 / 定稿
- 无项目前缀的（如 `完成failure-stack文档初始化`）直接去掉首词动词

跑法：
    python scripts/migrate_anchor_display.py            # dry-run，只打印 diff
    python scripts/migrate_anchor_display.py --apply    # 真写入 anchors.json + 自动备份
    python scripts/migrate_anchor_display.py --archive-stale-notes \
        --vault /path/to/Knowledge   # 把含「完成XX」前缀的 anchor note 文件移到 archive
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ANCHORS_PATH = Path("/Users/Harland/.keypulse/anchors.json")
DEFAULT_VAULT = Path("/Users/Harland/Go/Knowledge")
ANCHOR_NOTE_SUBDIR = "Anchors"

# 完成时动词
_VERBS = r"(?:完成了?|打通了?|上线了?|收官|定稿)"
# 模式 1: 项目前缀-动词宾语 → 项目前缀 宾语
_PROJECT_PREFIX_RE = re.compile(rf"^([^\-]+?)-{_VERBS}")
# 模式 2: 无前缀，首词是动词 → 去掉首词动词
_BARE_VERB_RE = re.compile(rf"^{_VERBS}")


def normalize_display(display: str) -> str:
    s = (display or "").strip()
    if not s:
        return s
    new = _PROJECT_PREFIX_RE.sub(lambda m: f"{m.group(1)} ", s)
    if new != s:
        return re.sub(r"\s+", " ", new).strip()
    new = _BARE_VERB_RE.sub("", s)
    return re.sub(r"\s+", " ", new).strip()


def load_anchors_payload() -> tuple[dict | list, list[dict]]:
    """Return (raw_payload, anchors_list_view)。anchors_list_view 是 anchors 列表的引用。"""
    payload = json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload, payload
    if isinstance(payload, dict):
        anchors = payload.get("anchors")
        if isinstance(anchors, list):
            return payload, anchors
        if isinstance(anchors, dict):
            return payload, list(anchors.values())
    raise ValueError(f"unexpected anchors.json shape: {type(payload).__name__}")


_STALE_NOTE_PATTERN = re.compile(rf"(?:^|-){_VERBS}")


def archive_stale_anchor_notes(vault_root: Path, *, apply: bool) -> int:
    notes_dir = vault_root / ANCHOR_NOTE_SUBDIR
    if not notes_dir.is_dir():
        print(f"[archive] anchors notes dir not found: {notes_dir}")
        return 0
    candidates = sorted(
        path
        for path in notes_dir.glob("*.md")
        if _STALE_NOTE_PATTERN.search(path.stem)
    )
    if not candidates:
        print("[archive] no stale anchor note files found.")
        return 0
    print(f"[archive] would move {len(candidates)} stale anchor note files:")
    for path in candidates:
        print(f"  {path.name}")
    if not apply:
        print("[archive] dry-run only. add --apply to move into archive dir.")
        return 0
    archive_dir = vault_root / f"_anchors-archive-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    archive_dir.mkdir(parents=True, exist_ok=False)
    for path in candidates:
        shutil.move(str(path), str(archive_dir / path.name))
    print(f"[archive] moved {len(candidates)} files to {archive_dir}")
    return len(candidates)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真写入（默认仅 dry-run）")
    parser.add_argument(
        "--archive-stale-notes",
        action="store_true",
        help="将 vault 里含「完成XX」前缀的 anchor note 文件移到 archive 子目录",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT,
        help=f"Obsidian vault 根目录（默认 {DEFAULT_VAULT}）",
    )
    args = parser.parse_args()

    if args.archive_stale_notes:
        return 0 if archive_stale_anchor_notes(args.vault, apply=args.apply) >= 0 else 1

    payload, anchors = load_anchors_payload()

    migrations: list[tuple[str, str, str]] = []
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        old = anchor.get("display") or ""
        new = normalize_display(old)
        if new and new != old:
            migrations.append((str(anchor.get("slug") or "?"), old, new))

    if not migrations:
        print("no anchors need migration. all clean ✓")
        return 0

    print(f"would migrate {len(migrations)} anchors:")
    for slug, old, new in migrations:
        print(f"  {slug}")
        print(f"    old: {old}")
        print(f"    new: {new}")

    if not args.apply:
        print()
        print("dry-run only. rerun with --apply to write changes.")
        return 0

    backup = ANCHORS_PATH.with_suffix(
        f".bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    shutil.copy(ANCHORS_PATH, backup)

    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        old = anchor.get("display") or ""
        new = normalize_display(old)
        if new and new != old:
            anchor["display"] = new

    ANCHORS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print()
    print(f"applied. backup at {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
