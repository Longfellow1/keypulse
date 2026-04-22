from __future__ import annotations

from keypulse.pipeline.contracts import PipelineInputs
from keypulse.pipeline.policy import LLMMode, build_pipeline_plan


def test_off_mode_disables_all_llm_calls():
    plan = build_pipeline_plan(
        LLMMode.OFF,
        PipelineInputs(event_count=80, candidate_count=20, topic_count=12, active_days=7),
    )

    assert plan.write.use_llm is False
    assert plan.write.mandatory_model_call is True
    assert plan.mine.use_llm is False
    assert plan.aggregate.use_llm is False


def test_balanced_mode_spends_llm_on_mine_before_write():
    plan = build_pipeline_plan(
        LLMMode.BALANCED,
        PipelineInputs(event_count=12, candidate_count=9, topic_count=4, active_days=2),
    )

    assert plan.write.use_llm is False
    assert plan.mine.use_llm is True
    assert plan.mine.max_items == 9
    assert plan.aggregate.use_llm is False


def test_high_mode_allows_weekly_aggregation():
    plan = build_pipeline_plan(
        LLMMode.HIGH,
        PipelineInputs(event_count=60, candidate_count=18, topic_count=14, active_days=9),
    )

    assert plan.write.use_llm is True
    assert plan.mine.use_llm is True
    assert plan.aggregate.use_llm is True
