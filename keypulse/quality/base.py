from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class Verdict:
    accept: bool
    reason: str = ""


class Strategy(ABC):
    """质量策略抽象基类。每条策略有唯一 id、版本、所属层、描述。"""

    id: ClassVar[str]
    version: ClassVar[str]
    layer: ClassVar[str]
    description: ClassVar[str]

    @abstractmethod
    def apply(self, value: str, context: dict[str, Any] | None = None) -> Verdict:
        ...
