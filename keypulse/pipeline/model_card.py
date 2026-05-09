"""Tier resolution for daily generation strategy.

A model is **flagship** when it crosses the ~150B-param line — slow, expensive,
high reasoning. Below that line is **budget** — fast, cheap, narrower attention.

Flagship → single-call daily generation (one prompt does clustering + narrative).
Budget → two-call daily generation (L1 cluster + L2 narrate, attention split).

Users can override per-backend in `config.toml`:
    [model.cloud]
    tier = "flagship"

Unknown models default to budget (conservative — degrade quality, never blow budget).
"""
from __future__ import annotations


# Substring match against lowercased model name. Order doesn't matter — first hit wins.
_FLAGSHIP_PATTERNS = (
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-v3",
    "deepseek-r1",
    "claude-opus",
    "claude-sonnet",
    "claude-3-5-sonnet",
    "claude-3-7-sonnet",
    "claude-4",
    "gpt-5",
    "gpt-4o",
    "gpt-4-turbo",
    "gemini-2.5-pro",
    "gemini-pro",
    "doubao-seed",
    "qwen2.5-72b",
    "qwen3-72b",
    "qwen-max",
)

VALID_TIERS = frozenset({"flagship", "budget"})


def resolve_tier(model_name: str | None, override: str | None = None) -> str:
    """Return "flagship" or "budget" for the given model.

    `override` (from user's `[model.*].tier` config) wins when valid.
    Empty / unknown override falls back to model-name pattern match.
    Models we don't recognize default to budget.
    """
    if override:
        normalized = override.strip().lower()
        if normalized in VALID_TIERS:
            return normalized
    name = (model_name or "").strip().lower()
    if not name:
        return "budget"
    for pattern in _FLAGSHIP_PATTERNS:
        if pattern in name:
            return "flagship"
    return "budget"
