#!/usr/bin/env python3
"""Lint and optionally clean topic notes under <vault>/Topics.

Default mode is dry-run. Pass --apply to execute delete/merge actions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keypulse.config import Config  # noqa: E402

_WIKI_LINK_RE = re.compile(r"\[\[(?P<target>[^\]|]+)(?:\|[^\]]+)?\]\]")
_MD_LINK_RE = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+)\)")
_FRONTMATTER_RE = re.compile(r"\A---\n(?P<fm>.*?)\n---\n?(?P<body>.*)\Z", re.DOTALL)


@dataclass
class TopicLint:
    path: Path
    relative_path: str
    category: str
    event_links: int
    has_keywords: bool
    has_narrative: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "category": self.category,
            "event_links": self.event_links,
            "has_keywords": self.has_keywords,
            "has_narrative": self.has_narrative,
            "reason": self.reason,
        }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lint and clean topic notes under <vault>/Topics")
    parser.add_argument(
        "--vault",
        default=Config.load().obsidian.vault_path,
        help="Vault path (default from config [obsidian].vault_path)",
    )
    parser.add_argument("--dry-run", action="store_true", default=True, help="Preview only (default).")
    parser.add_argument("--apply", action="store_true", help="Apply delete/merge operations.")
    return parser.parse_args()


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    return _parse_frontmatter(match.group("fm")), match.group("body")


def _parse_frontmatter(frontmatter: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    active_list_key: str | None = None

    for raw_line in frontmatter.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()

        if active_list_key and stripped.startswith("- "):
            parsed.setdefault(active_list_key, []).append(stripped[2:].strip())
            continue

        active_list_key = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if not value:
            parsed[key] = []
            active_list_key = key
            continue

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                parsed[key] = []
            else:
                parsed[key] = [item.strip().strip('"\'') for item in inner.split(",") if item.strip()]
            continue

        parsed[key] = value.strip('"\'')

    return parsed


def _extract_link_targets(text: str) -> list[str]:
    targets = [match.group("target").strip() for match in _WIKI_LINK_RE.finditer(text)]
    targets.extend(match.group("target").strip() for match in _MD_LINK_RE.finditer(text))
    return [target for target in targets if target]


def _is_event_target(target: str) -> bool:
    normalized = target.strip().lower()
    return (
        normalized.startswith("events/")
        or normalized.startswith("../.keypulse/events/")
        or "/events/" in normalized
    )


def _count_event_links(text: str) -> int:
    return sum(1 for target in _extract_link_targets(text) if _is_event_target(target))


def _is_frontmatter_only(text: str) -> bool:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return False
    return not match.group("body").strip()


def _has_keywords(frontmatter: dict[str, Any]) -> bool:
    value = frontmatter.get("keywords")
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    if isinstance(value, str):
        if "," in value:
            return any(part.strip() for part in value.split(","))
        return bool(value.strip())
    return False


def _body_has_narrative(body: str) -> bool:
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("- [[") and line.endswith("]]"):
            continue
        if _WIKI_LINK_RE.fullmatch(line):
            continue
        text_only = _WIKI_LINK_RE.sub("", line)
        text_only = _MD_LINK_RE.sub("", text_only)
        text_only = re.sub(r"[`*_\-\s\[\](){}:：/\\|]+", "", text_only)
        if len(text_only) >= 2:
            return True
    return False


def _has_narrative(frontmatter: dict[str, Any], body: str) -> bool:
    fm_narrative = frontmatter.get("narrative")
    if isinstance(fm_narrative, str) and fm_narrative.strip():
        return True
    return _body_has_narrative(body)


def _classify_topic(path: Path, base_dir: Path) -> TopicLint:
    relative_path = path.relative_to(base_dir).as_posix()
    size = path.stat().st_size
    if size == 0:
        return TopicLint(path, relative_path, "delete_candidate", 0, False, False, "empty file")

    text = path.read_text(encoding="utf-8")
    if _is_frontmatter_only(text):
        return TopicLint(path, relative_path, "delete_candidate", 0, False, False, "frontmatter only")

    frontmatter, body = _split_frontmatter(text)
    event_links = _count_event_links(text)
    keywords = _has_keywords(frontmatter)
    narrative = _has_narrative(frontmatter, body)

    if event_links < 2:
        return TopicLint(path, relative_path, "merge_candidate", event_links, keywords, narrative, "linked events < 2")
    if not keywords or not narrative:
        return TopicLint(
            path,
            relative_path,
            "enrich_candidate",
            event_links,
            keywords,
            narrative,
            "linked events >= 2 but missing keywords or narrative",
        )
    return TopicLint(path, relative_path, "healthy", event_links, keywords, narrative, "meets baseline")


def _backup_topics(topics_dir: Path, vault_path: Path) -> Path:
    backup_dir = vault_path / ".keypulse-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"topics-{date.today().isoformat()}.tar.gz"
    with tarfile.open(backup_path, "w:gz") as tar:
        tar.add(topics_dir, arcname="Topics")
    return backup_path


def _merge_candidates(merge_items: list[TopicLint], topics_dir: Path) -> tuple[int, Path | None]:
    if not merge_items:
        return 0, None

    merged_dir = topics_dir / "_merged"
    merged_dir.mkdir(parents=True, exist_ok=True)
    merged_file = merged_dir / "low-signal-topics.md"

    sections: list[str] = []
    if merged_file.exists():
        sections.append(merged_file.read_text(encoding="utf-8").rstrip())
    else:
        sections.append("# Low Signal Topic Merges")

    merged_count = 0
    for item in merge_items:
        if item.path == merged_file:
            continue
        text = item.path.read_text(encoding="utf-8")
        event_targets = [target for target in _extract_link_targets(text) if _is_event_target(target)]
        sections.extend(
            [
                "",
                f"## {item.relative_path}",
                "",
                f"- original_category: {item.category}",
                f"- linked_events: {item.event_links}",
                "",
                "### Evidence",
                *[f"- [[{target}]]" for target in event_targets],
                "",
            ]
        )
        item.path.unlink()
        merged_count += 1

    merged_file.write_text("\n".join(sections).rstrip() + "\n", encoding="utf-8")
    return merged_count, merged_file


def run_lint(vault_path: Path, *, apply: bool) -> dict[str, Any]:
    vault = vault_path.expanduser().resolve()
    topics_dir = vault / "Topics"
    if not topics_dir.exists():
        raise FileNotFoundError(f"Topics directory not found: {topics_dir}")

    topic_paths = sorted(path for path in topics_dir.rglob("*.md") if path.is_file())
    lint_items = [_classify_topic(path, topics_dir) for path in topic_paths]

    backup_path: Path | None = None
    deleted_count = 0
    merged_count = 0
    merged_output: Path | None = None

    if apply:
        backup_path = _backup_topics(topics_dir, vault)
        delete_items = [item for item in lint_items if item.category == "delete_candidate"]
        merge_items = [item for item in lint_items if item.category == "merge_candidate"]

        for item in delete_items:
            if item.path.exists():
                item.path.unlink()
                deleted_count += 1

        merged_count, merged_output = _merge_candidates(merge_items, topics_dir)

    counts: dict[str, int] = {
        "delete_candidate": 0,
        "merge_candidate": 0,
        "enrich_candidate": 0,
        "healthy": 0,
    }
    for item in lint_items:
        counts[item.category] += 1

    report = {
        "mode": "apply" if apply else "dry-run",
        "vault_path": str(vault),
        "topics_path": str(topics_dir),
        "total": len(lint_items),
        "counts": counts,
        "backup_path": str(backup_path) if backup_path else None,
        "actions": {
            "deleted": deleted_count,
            "merged": merged_count,
            "merged_output": str(merged_output) if merged_output else None,
        },
        "items": [item.as_dict() for item in lint_items],
    }
    return report


def render_markdown_report(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        "# Topics Lint Report",
        "",
        f"- mode: {report['mode']}",
        f"- vault: `{report['vault_path']}`",
        f"- topics: `{report['topics_path']}`",
        f"- total: {report['total']}",
        f"- delete_candidate: {counts['delete_candidate']}",
        f"- merge_candidate: {counts['merge_candidate']}",
        f"- enrich_candidate: {counts['enrich_candidate']}",
        f"- healthy: {counts['healthy']}",
        "",
        "| topic | category | events | keywords | narrative | reason |",
        "|---|---|---:|---|---|---|",
    ]

    for item in report["items"]:
        lines.append(
            "| {relative_path} | {category} | {event_links} | {has_keywords} | {has_narrative} | {reason} |".format(**item)
        )

    if report["backup_path"]:
        lines.extend(["", f"- backup: `{report['backup_path']}`"])
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    apply = bool(args.apply)
    vault_path = Path(args.vault)

    report = run_lint(vault_path, apply=apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print()
    print(render_markdown_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
