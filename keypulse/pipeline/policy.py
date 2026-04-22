from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from keypulse.pipeline.contracts import PipelineInputs, PipelinePlan, PipelineStage, StageBudget


class LLMMode(StrEnum):
    OFF = "off"
    BALANCED = "balanced"
    HIGH = "high"


def build_pipeline_plan(mode: LLMMode, inputs: PipelineInputs) -> PipelinePlan:
    off = mode == LLMMode.OFF
    balanced = mode == LLMMode.BALANCED
    high = mode == LLMMode.HIGH

    write_use_llm = (high or (balanced and inputs.event_count >= 24)) and not off
    mine_use_llm = (balanced or high) and not off and inputs.candidate_count > 0
    aggregate_use_llm = high and not off and inputs.topic_count >= 8 and inputs.active_days >= 7

    return PipelinePlan(
        write=StageBudget(
            PipelineStage.WRITE,
            write_use_llm,
            1 if write_use_llm else 0,
            "daily draft only when volume justifies it",
            mandatory_model_call=inputs.event_count > 0,
        ),
        mine=StageBudget(
            PipelineStage.MINE,
            mine_use_llm,
            min(inputs.candidate_count, 12) if mine_use_llm else 0,
            "rank only the best candidates",
            mandatory_model_call=mine_use_llm,
        ),
        aggregate=StageBudget(
            PipelineStage.AGGREGATE,
            aggregate_use_llm,
            min(inputs.topic_count, 8) if aggregate_use_llm else 0,
            "weekly theme consolidation",
            mandatory_model_call=aggregate_use_llm,
        ),
    )
