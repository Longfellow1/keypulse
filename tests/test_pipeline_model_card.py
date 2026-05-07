from __future__ import annotations

import pytest

from keypulse.pipeline.model_card import resolve_tier


@pytest.mark.parametrize(
    "model_name",
    [
        "deepseek-chat",
        "claude-sonnet-4",
        "gpt-5.1",
        "gemini-2.5-pro",
        "doubao-seed-1-6-250615",
    ],
)
def test_resolve_tier_known_flagship_models(model_name):
    assert resolve_tier(model_name) == "flagship"


def test_resolve_tier_unknown_model_defaults_budget():
    assert resolve_tier("qwen2.5-7b-instruct") == "budget"
    assert resolve_tier("") == "budget"
    assert resolve_tier(None) == "budget"


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("flagship", "flagship"),
        ("budget", "budget"),
        (" FLAGSHIP ", "flagship"),
    ],
)
def test_resolve_tier_valid_override_wins(override, expected):
    assert resolve_tier("qwen2.5-7b", override=override) == expected


@pytest.mark.parametrize("override", ["", "cheap", "premium", None])
def test_resolve_tier_invalid_or_empty_override_falls_back(override):
    assert resolve_tier("deepseek-chat", override=override) == "flagship"
    assert resolve_tier("unknown-small-model", override=override) == "budget"
