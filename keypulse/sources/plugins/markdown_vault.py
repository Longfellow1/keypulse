from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent


class MarkdownVaultSource(DataSource):
    name = "markdown_vault"
    privacy_tier = "green"
    liveness = "always"
    description = "Obsidian/Logseq markdown vault metadata reader"

    def __init__(self, *, roots: list[Path] | None = None) -> None:
        home = Path.home()
        self._roots = roots or [home, home / "Documents", home / "Go", home / "Code", home / "Notes"]

    def discover(self) -> list[DataSourceInstance]:
        instances: dict[str, DataSourceInstance] = {}
        for root in self._roots:
            if not root.exists() or not root.is_dir():
                continue
            for obsidian_dir in root.rglob(".obsidian"):
                if not obsidian_dir.is_dir():
                    continue
                vault_root = obsidian_dir.parent.resolve()
                key = str(vault_root)
                if key in instances:
                    continue
                note_count = self._count_notes(vault_root)
                vault_name = vault_root.name or "vault"
                instances[key] = DataSourceInstance(
                    plugin=self.name,
                    locator=key,
                    label=vault_name,
                    metadata={"note_count": note_count, "vault_name": vault_name},
                )
        return sorted(instances.values(), key=lambda item: item.locator)

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        vault_root = Path(instance.locator).expanduser()
        if not vault_root.exists() or not vault_root.is_dir():
            return iter(())

        vault_name = str(instance.metadata.get("vault_name") or vault_root.name or "vault")

        def _iter_events() -> Iterator[SemanticEvent]:
            for path in sorted(vault_root.rglob("*.md")):
                if ".obsidian" in path.parts:
                    continue
                try:
                    stat = path.stat()
                except Exception:
                    continue
                event_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if event_time < since or event_time > until:
                    continue

                first_line, tags = _read_header_only(path)
                if not first_line:
                    continue

                try:
                    rel_path = str(path.relative_to(vault_root))
                except Exception:
                    rel_path = path.name

                intent = first_line.lstrip("# ").strip()[:200]
                if not intent:
                    continue
                yield SemanticEvent(
                    time=event_time,
                    source=self.name,
                    actor="user",
                    intent=intent,
                    artifact=rel_path,
                    raw_ref=f"markdown_vault:{vault_name}:{rel_path}",
                    privacy_tier=self.privacy_tier,
                    metadata={
                        "vault_name": vault_name,
                        "frontmatter_tags": tags,
                        "file_size": stat.st_size,
                    },
                )

        return _iter_events()

    def _count_notes(self, vault_root: Path) -> int:
        count = 0
        for path in vault_root.rglob("*.md"):
            if ".obsidian" in path.parts:
                continue
            count += 1
        return count


def _read_header_only(path: Path) -> tuple[str, list[str]]:
    tags: list[str] = []
    first_line = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = [handle.readline() for _ in range(80)]
    except Exception:
        return "", tags

    if lines and lines[0].strip() == "---":
        end_idx = -1
        for idx in range(1, len(lines)):
            if lines[idx].strip() == "---":
                end_idx = idx
                break
        if end_idx != -1:
            tags = _parse_tags(lines[1:end_idx])
            content_start = end_idx + 1
            while content_start < len(lines) and not lines[content_start]:
                content_start += 1
            for line in lines[content_start:]:
                stripped = line.strip()
                if stripped:
                    first_line = stripped
                    break
        else:
            first_line = lines[0].strip()
    else:
        for line in lines:
            stripped = line.strip()
            if stripped:
                first_line = stripped
                break
    return first_line, tags


def _parse_tags(frontmatter_lines: list[str]) -> list[str]:
    tags: list[str] = []
    for line in frontmatter_lines:
        stripped = line.strip()
        if stripped.startswith("tags:"):
            value = stripped.split(":", 1)[1].strip()
            if value.startswith("[") and value.endswith("]"):
                items = value[1:-1].split(",")
                tags.extend(item.strip().strip("'\"") for item in items if item.strip())
            elif value:
                tags.append(value.strip().strip("'\""))
    return [tag for tag in tags if tag]
