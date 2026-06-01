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
    python scripts/migrate_anchor_display.py --apply    # 真写入，自动备份
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真写入（默认仅 dry-run）")
    args = parser.parse_args()

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
