from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NoteCard:
    path: str
    properties: dict[str, Any] = field(default_factory=dict)
    body: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "properties": dict(self.properties),
            "body": self.body,
        }
