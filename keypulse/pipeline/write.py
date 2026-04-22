from __future__ import annotations

import logging
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from keypulse.pipeline.decisions import build_daily_decisions, render_daily_decisions
from keypulse.pipeline.feedback import FeedbackEvent, summarize_feedback_events
from keypulse.pipeline.narrative import aggregate_work_blocks, render_daily_narrative
from keypulse.pipeline.record import normalize_record_events
from keypulse.pipeline.model import ModelGateway

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyDraft:
    body: str
    llm_used: bool
    event_count: int
    model_name: str = ""
    prompt_hash: str = ""


def _build_prompt(inputs: Any, events: list[dict[str, Any]]) -> str:
    normalized = normalize_record_events(events)
    lines = [
        "# Daily Draft",
        "",
        f"- Events: {len(normalized)}",
        f"- Candidates: {getattr(inputs, 'candidate_count', 0)}",
        f"- Topics: {getattr(inputs, 'topic_count', 0)}",
        "",
        "## Events",
    ]
    for event in normalized:
        lines.append(f"- {event.title}: {event.body}")
    return "\n".join(lines)


def build_daily_draft(
    inputs: Any,
    events: list[dict[str, Any]],
    *,
    model_gateway: ModelGateway | None = None,
    plan: Any | None = None,
    feedback_events: list[FeedbackEvent] | None = None,
) -> DailyDraft:
    normalized = normalize_record_events(events)
    work_blocks = aggregate_work_blocks(events)
    feedback_summary = summarize_feedback_events(feedback_events or [])
    prompt_seed = json.dumps(
        {
            "inputs": {
                "event_count": getattr(inputs, "event_count", 0),
                "candidate_count": getattr(inputs, "candidate_count", 0),
                "topic_count": getattr(inputs, "topic_count", 0),
                "active_days": getattr(inputs, "active_days", 0),
            },
            "work_blocks": [block.__dict__ for block in work_blocks],
            "feedback": feedback_summary,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    prompt_hash = hashlib.sha256(prompt_seed.encode("utf-8")).hexdigest()[:16]

    narrative_body = render_daily_narrative(work_blocks)
    llm_used = False
    model_name = ""

    if model_gateway is not None:
        backend = model_gateway.select_backend("write") if hasattr(model_gateway, "select_backend") else None
        backend_kind = getattr(backend, "kind", "") if backend is not None else ""
        backend_model = getattr(backend, "model", "") if backend is not None else ""
        backend_available = backend is None or backend_kind != "disabled"
        model_name = backend_model or backend_kind or getattr(model_gateway, "active_profile", "")
        if backend_available and hasattr(model_gateway, "render_daily_narrative"):
            prompt_patch = "\n".join(
                part
                for part in (
                    f"profile={model_gateway.active_profile}",
                    f"backend={backend_kind or getattr(model_gateway, 'active_profile', '')}",
                    f"model={backend_model or getattr(model_gateway, 'active_profile', '')}",
                    f"feedback={feedback_summary}",
                )
                if part
            )
            try:
                narrative_body = model_gateway.render_daily_narrative(work_blocks, prompt_patch=prompt_patch)
                llm_used = True
            except Exception as exc:
                logger.error("daily_narrative fallback backend_kind=%s url=%s model=%s exc_type=%s exc=%s", backend_kind, getattr(backend, "base_url", ""), backend_model or backend_kind or getattr(model_gateway, "active_profile", ""), type(exc).__name__, exc)
                narrative_body = render_daily_narrative(work_blocks)

    decisions = build_daily_decisions(work_blocks, feedback_events=feedback_events)
    body = "\n\n".join(
        [
            narrative_body.strip(),
            render_daily_decisions(decisions),
        ]
    ).strip()

    return DailyDraft(
        body=body,
        llm_used=llm_used,
        event_count=len(normalized),
        model_name=model_name,
        prompt_hash=prompt_hash,
    )
