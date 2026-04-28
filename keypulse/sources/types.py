from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator


@dataclass
class SemanticEvent:
    time: datetime
    source: str
    actor: str
    intent: str
    artifact: str
    raw_ref: str
    privacy_tier: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.time.tzinfo is None or self.time.utcoffset() is None:
            raise ValueError("SemanticEvent.time must be timezone-aware")
        self.time = self.time.astimezone(timezone.utc)


@dataclass
class DataSourceInstance:
    plugin: str
    locator: str
    label: str
    metadata: dict[str, Any] = field(default_factory=dict)


class DataSource(ABC):
    name: str
    privacy_tier: str
    liveness: str
    description: str = ""

    @abstractmethod
    def discover(self) -> list[DataSourceInstance]:
        pass

    @abstractmethod
    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        pass
