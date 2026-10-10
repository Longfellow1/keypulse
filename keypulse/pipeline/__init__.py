"""keypulse.pipeline public names, resolved lazily (PEP 562).

Importing a pipeline submodule must not eagerly pull the whole package
(model gateway, surface, themes, ...). Names below load on first access.
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from keypulse.pipeline.aggregate import ThemeSummary, build_theme_summary
    from keypulse.pipeline.contracts import PipelineInputs, PipelinePlan, PipelineStage, StageBudget
    from keypulse.pipeline.feedback import (
        FeedbackEvent,
        append_feedback_event,
        current_theme_profile,
        read_feedback_events,
        summarize_feedback_events,
        record_theme_feedback,
    )
    from keypulse.pipeline.model import ModelBackend, ModelGateway, load_model_gateway
    from keypulse.pipeline.record import RecordEvent, normalize_record_events
    from keypulse.pipeline.themes import ThemeProfile, read_theme_profile, record_theme_refine, write_theme_profile
    from keypulse.pipeline.surface import build_surface_snapshot, translate_why_selected
    from keypulse.pipeline.policy import LLMMode, build_pipeline_plan

_LAZY = {
    "ThemeSummary": "keypulse.pipeline.aggregate",
    "build_theme_summary": "keypulse.pipeline.aggregate",
    "PipelineInputs": "keypulse.pipeline.contracts",
    "PipelinePlan": "keypulse.pipeline.contracts",
    "PipelineStage": "keypulse.pipeline.contracts",
    "StageBudget": "keypulse.pipeline.contracts",
    "FeedbackEvent": "keypulse.pipeline.feedback",
    "append_feedback_event": "keypulse.pipeline.feedback",
    "current_theme_profile": "keypulse.pipeline.feedback",
    "read_feedback_events": "keypulse.pipeline.feedback",
    "summarize_feedback_events": "keypulse.pipeline.feedback",
    "record_theme_feedback": "keypulse.pipeline.feedback",
    "ModelBackend": "keypulse.pipeline.model",
    "ModelGateway": "keypulse.pipeline.model",
    "load_model_gateway": "keypulse.pipeline.model",
    "RecordEvent": "keypulse.pipeline.record",
    "normalize_record_events": "keypulse.pipeline.record",
    "ThemeProfile": "keypulse.pipeline.themes",
    "read_theme_profile": "keypulse.pipeline.themes",
    "record_theme_refine": "keypulse.pipeline.themes",
    "write_theme_profile": "keypulse.pipeline.themes",
    "build_surface_snapshot": "keypulse.pipeline.surface",
    "translate_why_selected": "keypulse.pipeline.surface",
    "LLMMode": "keypulse.pipeline.policy",
    "build_pipeline_plan": "keypulse.pipeline.policy",
}

__all__ = [
    "FeedbackEvent",
    "LLMMode",
    "ModelBackend",
    "ModelGateway",
    "PipelineInputs",
    "PipelinePlan",
    "PipelineStage",
    "RecordEvent",
    "StageBudget",
    "ThemeSummary",
    "ThemeProfile",
    "append_feedback_event",
    "build_pipeline_plan",
    "build_surface_snapshot",
    "build_theme_summary",
    "current_theme_profile",
    "load_model_gateway",
    "normalize_record_events",
    "read_theme_profile",
    "read_feedback_events",
    "record_theme_feedback",
    "record_theme_refine",
    "summarize_feedback_events",
    "translate_why_selected",
    "write_theme_profile",
]


def __getattr__(name: str) -> Any:
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(mod), name)
    globals()[name] = value
    return value
