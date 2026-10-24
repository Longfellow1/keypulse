"""Pure path selection rules shared by source discovery and its schema migrations."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def approved_markdown_vault_roots(
    records: Iterable[object], *, document_shape: str = "document_file"
) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()
    for record in records:
        metadata = getattr(record, "metadata", {})
        if metadata.get("discoverer") != "markdown_vault":
            continue
        shape = str(metadata.get("shape") or "").strip()
        if shape and shape != document_shape:
            continue
        raw_path = str(metadata.get("path") or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve(strict=False)
        key = str(path)
        if key not in seen:
            seen.add(key)
            roots.append(path)
    return roots
