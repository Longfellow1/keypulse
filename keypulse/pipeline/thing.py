from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from keypulse.pipeline.entity_extractor import Entity
from keypulse.sources.types import SemanticEvent


@dataclass
class Thing:
    id: str
    title: str
    entities: list[Entity]
    events: list[SemanticEvent]
    time_start: datetime
    time_end: datetime
    sources: set[str]
    narrative: str = ""
