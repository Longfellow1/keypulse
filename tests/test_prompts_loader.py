from __future__ import annotations

import pytest

from keypulse.prompts.loader import PromptCapabilityNotFoundError, load_prompt


@pytest.mark.parametrize(
    "capability,version,model_tier,max_tokens,body_marker",
    [
        ("daily_flagship", "v1", "standard", 4000, "整篇日报内容"),
        ("L1_cluster_review", "v1", "standard", 800, "只输出 JSON"),
        ("L2_narrative", "v2", "standard", 4000, "整篇日报内容"),
        ("L3_topic_naming", "v1", "mini", 300, "新主题命名器"),
        ("L4_weekly_reconcile", "v1", "standard", 800, "typed JSON merge 决策"),
        ("L5_weekly_main_narrative", "v1", "standard", 1500, "这周的主线"),
        ("L6_explorer", "v1", "standard", 600, "这周的回声"),
    ],
)
def test_load_prompt_for_all_capabilities(capability, version, model_tier, max_tokens, body_marker):
    spec = load_prompt(capability)

    assert spec.capability == capability
    assert spec.version == version
    assert spec.model_tier == model_tier
    assert spec.max_tokens == max_tokens
    assert spec.temperature > 0
    assert body_marker in spec.body
    assert isinstance(spec.input_schema, dict)
    assert isinstance(spec.output_schema, dict)
    assert spec.input_schema.get("type") == "object"
    assert spec.output_schema.get("type") == "object"


def test_load_prompt_supports_short_aliases():
    spec = load_prompt("L1")

    assert spec.capability == "L1_cluster_review"


def test_load_prompt_raises_specific_error_for_missing_capability():
    with pytest.raises(PromptCapabilityNotFoundError, match="prompt capability not found"):
        load_prompt("unknown_capability")
