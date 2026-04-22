from __future__ import annotations

from keypulse.pipeline.cache import candidate_cache_key
from keypulse.pipeline.contracts import PipelineInputs
from keypulse.pipeline.mine import select_mining_candidates


def test_select_mining_candidates_returns_top_scored_items_when_budget_allows():
    candidates = select_mining_candidates(
        PipelineInputs(event_count=50, candidate_count=6, topic_count=2, active_days=1),
        items=[
            {"title": "Open terminal", "score": 0.1},
            {"title": "Write design note", "score": 0.9},
            {"title": "Copy snippet", "score": 0.8},
        ],
        llm_budget_remaining=1,
    )

    assert [item["title"] for item in candidates] == ["Write design note"]


def test_candidate_cache_key_is_stable_across_item_order():
    items_a = [
        {"title": "Write design note", "score": 0.9},
        {"title": "Copy snippet", "score": 0.8},
    ]
    items_b = list(reversed(items_a))

    assert candidate_cache_key(items_a) == candidate_cache_key(items_b)
