from __future__ import annotations

from typing import Any

import pytest

from keypulse.pipeline.daily_strategy import (
    BudgetStrategyDeps,
    BudgetTwoStepStrategy,
    DailyStrategyError,
    FlagshipSingleStepStrategy,
    extract_work_unit,
    to_compact_event,
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
        self.prompts: list[str] = []

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs) -> Any:
        self.calls.append((capability, input_data))
        self.prompts.append(prompt)
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


def test_flagship_strategy_prepends_repair_hint_before_prompt_body():
    gateway = FakeGateway({"daily_flagship": {"markdown": MARKDOWN}})

    FlagshipSingleStepStrategy().generate(
        date_str="2026-05-01",
        events=_events(),
        gateway=gateway,
        repair_hint="things<3，请重写到至少3个H3",
    )

    prompt = gateway.prompts[0]
    assert "REPAIR MODE" in prompt
    assert "things<3，请重写到至少3个H3" in prompt
    assert prompt.index("REPAIR MODE") < prompt.index("<<INPUT_JSON>>")


def test_to_compact_event_keeps_scene_fingerprint_fields():
    compact = to_compact_event(
        {
            "id": "evt-1",
            "session_id": "top-session",
            "ts_start": "2026-05-01T01:00:00+00:00",
            "source": "codex_cli",
            "speaker": "user",
            "app_name": "Codex",
            "window_title": "KeyPulse P1 daily pipeline现场指纹扩展" * 4,
            "content_text": "x" * 400,
            "metadata_json": {
                "entities": {
                    "session_id": "metadata-session",
                    "file_paths": [
                        "/Users/Harland/Go/keypulse/keypulse/pipeline/daily_strategy.py",
                        "/Users/Harland/Go/keypulse/docs/raw-events-metadata-schema.md",
                        "/Users/Harland/Go/keypulse/tests/test_pipeline_daily_strategy.py",
                        "/Users/Harland/Go/keypulse/extra.py",
                    ],
                    "urls": ["https://github.com/example/keypulse"],
                }
            },
        }
    )

    assert compact["eid"] == "evt-1"
    assert compact["sid"] == "top-session"
    assert compact["win"] == ("KeyPulse P1 daily pipeline现场指纹扩展" * 4)[:80]
    assert compact["wu"] == "daily_strategy"
    assert compact["fp"] == [
        "/Users/Harland/Go/keypulse/keypulse/pipeline/daily_strategy.py"[:60],
        "/Users/Harland/Go/keypulse/docs/raw-events-metadata-schema.md"[:60],
        "/Users/Harland/Go/keypulse/tests/test_pipeline_daily_strategy"[:60],
    ]
    assert compact["url"] == "https://github.com/example/keypulse"
    assert len(compact["c"]) == 320


def test_extract_work_unit_prefers_window_title_and_decodes_claude_project_paths():
    assert (
        extract_work_unit({"window_title": "Agent系统的真正瓶颈：分层、标准化与评测 - Claude"})
        == "Agent系统的真正瓶颈：分层、标准化与评测"
    )
    assert (
        extract_work_unit(
            {
                "metadata": {
                    "entities": {
                        "file_paths": [
                            "/Users/Harland/.claude/projects/-Users-Harland-Go-Chat-CHAT-0410/sess.jsonl"
                        ]
                    }
                }
            }
        )
        == "CHAT-0410"
    )


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


def test_budget_strategy_orders_l2_clusters_by_peak_density_then_time():
    component_payloads = [
        {
            "component_id": "c1",
            "event_ids": ["1", "2", "3"],
            "time_range": ["01:00", "01:03"],
            "h1_entities": [],
            "h2_contexts": [],
            "keywords": ["routine"],
            "peak_event_density": 0.2,
        },
        {
            "component_id": "c2",
            "event_ids": ["4"],
            "time_range": ["03:00", "03:00"],
            "h1_entities": [],
            "h2_contexts": [],
            "keywords": ["decision"],
            "peak_event_density": 0.95,
        },
        {
            "component_id": "c3",
            "event_ids": ["5"],
            "time_range": ["04:00", "04:00"],
            "h1_entities": [],
            "h2_contexts": [],
            "keywords": ["tool"],
            "peak_event_density": 0.25,
        },
    ]
    deps = BudgetStrategyDeps(
        cluster_components=lambda _events: component_payloads,
        load_topics_index=lambda: [],
        load_hot_slugs=lambda: [],
        prune_topics=lambda topics, _hot, _events: topics,
        topic_display_name=lambda slug, _topics: slug,
        detect_merges=lambda _components: [],
    )
    gateway = FakeGateway(
        {
            "L1_cluster_review": {
                "clusters": [
                    {"component_id": "c1", "topic_action": "new", "reason": "routine"},
                    {"component_id": "c2", "topic_action": "new", "reason": "decision"},
                    {"component_id": "c3", "topic_action": "new", "reason": "tool echo"},
                ],
                "misc_event_ids": [],
            },
            "L2_narrative": {"markdown": MARKDOWN},
        }
    )
    events = [
        {"id": "1", "ts_start": "2026-05-01T01:00:00+00:00", "source": "ax_text", "speaker": "system", "app_name": "Terminal", "content_text": "routine"},
        {"id": "2", "ts_start": "2026-05-01T01:01:00+00:00", "source": "ax_text", "speaker": "system", "app_name": "Terminal", "content_text": "routine"},
        {"id": "3", "ts_start": "2026-05-01T01:02:00+00:00", "source": "ax_text", "speaker": "system", "app_name": "Terminal", "content_text": "routine"},
        {"id": "4", "ts_start": "2026-05-01T03:00:00+00:00", "source": "clipboard", "speaker": "user", "app_name": "Chrome", "content_text": "我建议选择方案 B，根因是入口职责需要拆开"},
        {"id": "5", "ts_start": "2026-05-01T04:00:00+00:00", "source": "ax_text", "speaker": "system", "app_name": "Terminal", "content_text": "When using Powerlevel10k with instant prompt"},
    ]

    BudgetTwoStepStrategy(deps).generate(date_str="2026-05-01", events=events, gateway=gateway)

    l2_input = gateway.calls[1][1]
    assert [cluster["component_id"] for cluster in l2_input["clusters"]] == ["c2", "c3", "c1"]


def test_budget_strategy_keeps_single_event_misc_without_density_promotion():
    component_payloads = [
        {
            "component_id": "c1",
            "event_ids": ["1"],
            "time_range": ["03:00", "03:00"],
            "h1_entities": [],
            "h2_contexts": [],
            "keywords": ["decision"],
            "peak_event_density": 0.9,
        }
    ]
    deps = BudgetStrategyDeps(
        cluster_components=lambda _events: component_payloads,
        load_topics_index=lambda: [],
        load_hot_slugs=lambda: [],
        prune_topics=lambda topics, _hot, _events: topics,
        topic_display_name=lambda slug, _topics: slug,
        detect_merges=lambda _components: [],
    )
    gateway = FakeGateway(
        {
            "L1_cluster_review": {
                "clusters": [{"component_id": "c1", "topic_action": "misc", "reason": "single event"}],
                "misc_event_ids": ["1"],
            },
            "L2_narrative": {"markdown": MARKDOWN},
        }
    )

    result = BudgetTwoStepStrategy(deps).generate(date_str="2026-05-01", events=[_events()[0]], gateway=gateway)

    l2_input = gateway.calls[1][1]
    assert l2_input["clusters"] == []
    assert len(l2_input["misc_events"]) == 1
    assert result.misc_event_ids == ("1",)


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
