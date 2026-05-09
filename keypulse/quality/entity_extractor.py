from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from keypulse.sources.types import SemanticEvent

_EXTRACT_IMPL = None


def extract_named_entities(event: SemanticEvent) -> list[str]:
    """Extract de-duplicated named entities for raw_events metadata storage."""
    global _EXTRACT_IMPL
    if _EXTRACT_IMPL is None:
        from keypulse.pipeline.entity_extractor import extract as extract_impl

        _EXTRACT_IMPL = extract_impl

    named_entities: list[str] = []
    seen: set[str] = set()
    for entity in _EXTRACT_IMPL(event):
        value = str(entity.value or "").strip()
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        named_entities.append(value)
    return named_entities
