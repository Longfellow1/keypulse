from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PipelineStage(StrEnum):
    RECORD = "record"
    WRITE = "write"
    MINE = "mine"
    AGGREGATE = "aggregate"
    SURFACE = "surface"
    FEEDBACK = "feedback"


@dataclass(frozen=True)
class PipelineInputs:
    event_count: int
    candidate_count: int
    topic_count: int
    active_days: int
    llm_calls_used: int = 0
    llm_input_chars_used: int = 0


@dataclass(frozen=True)
class StageBudget:
    stage: PipelineStage
    use_llm: bool
    max_items: int
    reason: str
    mandatory_model_call: bool = False


@dataclass(frozen=True)
class PipelinePlan:
    write: StageBudget
    mine: StageBudget
    aggregate: StageBudget
