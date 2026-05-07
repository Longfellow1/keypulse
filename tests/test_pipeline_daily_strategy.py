from __future__ import annotations

from typing import Any

import pytest

from keypulse.pipeline.daily_strategy import (
    BudgetStrategyDeps,
    BudgetTwoStepStrategy,
    DailyStrategyError,
    FlagshipSingleStepStrategy,
)
from keypulse.pipeline.model import LLMCallError


MARKDOWN = """📍 Asia/Shanghai

# 2026-05-01

## 今日要点

你今天把 daily 生成链路拆成按模型能力选择的两条路径，减少预算模型的重复叙事调用，同时让旗舰模型直接承担整篇日报生成，降低编排层复杂度。

## 今天做的事

### KeyPulse Daily Strategy

你围绕 KeyPulse daily 管线完成策略拆分，保留聚类和主题维护给预算路径，把旗舰路径压缩为一次整篇生成。这让调用次数和输出质量的边界更明确。

## 明日的锚点

> 明天我想：______
>
> _写一句话留给明天的自己_
"""


class FakeGateway:
    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple[str, Any]] = []

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs) -> Any:
        self.calls.append((capability, input_data))
        response = self.responses[capability]
        if isinstance(response, BaseException):
            raise response
        return response


def _events() -> list[dict[str, Any]]:
    return [
        {"id": "1", "ts_start": "2026-05-01T01:00:00+00:00", "source": "ax_text", "speaker": "user", "app_name": "Codex", "content_text": "KeyPulse daily strategy"},
        {"id": "2", "ts_start": "2026-05-01T01:03:00+00:00", "source": "ax_text", "speaker": "ai", "app_name": "Codex", "content_text": "Budget L2 should run once"},
        {"id": "3", "ts_start": "2026-05-01T03:00:00+00:00", "source": "clipboard", "speaker": "user", "app_name": "Chrome", "content_text": "misc browsing"},
    ]


def _deps() -> BudgetStrategyDeps:
    component_payloads = [
        {
            "component_id": "c1",
            "event_ids": ["1", "2"],
            "time_range": ["01:00", "01:03"],
            "h1_entities": ["ne:keypulse"],
            "h2_contexts": [],
            "keywords": ["keypulse", "daily", "strategy"],
        },
        {
            "component_id": "c2",
            "event_ids": ["3"],
            "time_range": ["03:00", "03:00"],
            "h1_entities": [],
            "h2_contexts": [],
            "keywords": ["misc"],
        },
    ]
    return BudgetStrategyDeps(
        cluster_components=lambda _events: component_payloads,
        load_topics_index=lambda: [{"slug": "existing-topic", "display_name": "Existing Topic", "keywords": ["existing"], "last_seen": "2026-04-30", "status": "active"}],
        load_hot_slugs=lambda: ["existing-topic"],
        prune_topics=lambda topics, _hot, _events: topics,
        topic_display_name=lambda slug, _topics: "Existing Topic" if slug == "existing-topic" else slug,
        detect_merges=lambda _components: [("c1", "c2")],
    )


def test_flagship_strategy_calls_daily_flagship_once_and_returns_markdown():
    gateway = FakeGateway({"daily_flagship": {"markdown": MARKDOWN}})

    result = FlagshipSingleStepStrategy().generate(date_str="2026-05-01", events=_events(), gateway=gateway)

    assert [capability for capability, _input in gateway.calls] == ["daily_flagship"]
    assert gateway.calls[0][1]["events"][0]["s"] == "ax_text"
    assert gateway.calls[0][1]["events"][1]["sp"] == "ai"
    assert result.markdown == MARKDOWN.strip()
    assert result.clusters == ()


def test_budget_strategy_calls_l1_and_one_l2_and_tracks_misc():
    gateway = FakeGateway(
        {
            "L1_cluster_review": {
                "clusters": [
                    {"component_id": "c1", "topic_action": "new", "reason": "new topic"},
                    {"component_id": "c2", "topic_action": "misc", "reason": "noise"},
                ],
                "misc_event_ids": ["3"],
            },
            "L2_narrative": {"markdown": MARKDOWN},
        }
    )

    result = BudgetTwoStepStrategy(_deps()).generate(date_str="2026-05-01", events=_events(), gateway=gateway)

    assert [capability for capability, _input in gateway.calls] == ["L1_cluster_review", "L2_narrative"]
    assert gateway.calls[1][1]["clusters"][0]["display_name"] == "主题-c1"
    assert gateway.calls[1][1]["misc_events"][0]["c"] == "misc browsing"
    assert result.markdown == MARKDOWN.strip()
    assert result.misc_event_ids == ("3",)
    assert len(result.clusters) == 1
    assert result.clusters[0].component_id == "c1"
    assert result.merge_candidates == (("c1", "c2"),)


@pytest.mark.parametrize(
    "strategy,gateway",
    [
        (FlagshipSingleStepStrategy(), FakeGateway({"daily_flagship": {"markdown": ""}})),
        (BudgetTwoStepStrategy(_deps()), FakeGateway({"L1_cluster_review": {"clusters": [], "misc_event_ids": []}, "L2_narrative": {"markdown": ""}})),
    ],
)
def test_strategy_rejects_empty_markdown(strategy, gateway):
    with pytest.raises(DailyStrategyError, match="empty|returned"):
        strategy.generate(date_str="2026-05-01", events=_events(), gateway=gateway)


@pytest.mark.parametrize(
    "strategy,gateway",
    [
        (FlagshipSingleStepStrategy(), FakeGateway({"daily_flagship": LLMCallError("boom")})),
        (BudgetTwoStepStrategy(_deps()), FakeGateway({"L1_cluster_review": LLMCallError("boom")})),
    ],
)
def test_strategy_wraps_llm_call_errors(strategy, gateway):
    with pytest.raises(DailyStrategyError, match="failed"):
        strategy.generate(date_str="2026-05-01", events=_events(), gateway=gateway)
