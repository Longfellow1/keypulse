from __future__ import annotations

import logging
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keypulse.pipeline.decisions import build_daily_decisions, render_daily_decisions
from keypulse.pipeline.evidence import enrich_with_evidence, load_profile_dict
from keypulse.pipeline.feedback import FeedbackEvent, summarize_feedback_events
from keypulse.pipeline.gates import should_generate_narrative, summarize_quality
from keypulse.pipeline.narrative import aggregate_work_blocks, render_daily_narrative
from keypulse.pipeline.narrative_v2 import render_v2_narrative
from keypulse.pipeline.skeleton import build_daily_skeleton_report
from keypulse.pipeline.record import normalize_record_events
from keypulse.pipeline.signals import collect_browser_signals, collect_filesystem_signals, enrich_unit_with_signals
from keypulse.pipeline.model import ModelGateway

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyDraft:
    body: str
    llm_used: bool
    event_count: int
    model_name: str = ""
    prompt_hash: str = ""


def _render_event_cards(events: list[Any]) -> str:
    lines = ["## 今天的事件卡", ""]
    if not events:
        lines.append("- 今天没有可展示的事件卡")
        return "\n".join(lines).strip()

    for event in events:
        ts = str(getattr(event, "ts", "") or "").strip()
        title = str(getattr(event, "title", "") or "event").strip() or "event"
        body = str(getattr(event, "body", "") or "").strip()
        headline = f"- {ts} {title}".strip()
        lines.append(headline)
        if body and body != title:
            lines.append(f"  - {body[:120]}")
    return "\n".join(lines).strip()


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
    use_narrative_v2: bool = False,
    use_narrative_skeleton: bool = False,
    db_path: Path | None = None,
    date_str: str = "",
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
    skeleton_rendered = False

    if use_narrative_skeleton and model_gateway is not None and db_path is not None:
        backend = model_gateway.select_backend("write") if hasattr(model_gateway, "select_backend") else None
        backend_kind = getattr(backend, "kind", "") if backend is not None else ""
        backend_model = getattr(backend, "model", "") if backend is not None else ""
        model_name = backend_model or backend_kind or getattr(model_gateway, "active_profile", "")
        try:
            narrative_body = build_daily_skeleton_report(db_path, date_str, model_gateway)
            llm_used = True
            skeleton_rendered = True
        except Exception as exc:
            logger.warning(
                "skeleton narrative failure backend_kind=%s model=%s exc_type=%s exc=%s; falling back to v2",
                backend_kind,
                backend_model or backend_kind or getattr(model_gateway, "active_profile", ""),
                type(exc).__name__,
                exc,
            )
            try:
                v2_body = render_v2_narrative(
                    work_blocks,
                    model_gateway=model_gateway,
                    db_path=db_path,
                    date_str=date_str,
                )
                if v2_body:
                    narrative_body = v2_body
                    llm_used = not v2_body.startswith("今日证据不足")
            except Exception as fallback_exc:
                logger.warning(
                    "skeleton fallback to v2 failed backend_kind=%s model=%s exc_type=%s exc=%s; using legacy narrative",
                    backend_kind,
                    backend_model or backend_kind or getattr(model_gateway, "active_profile", ""),
                    type(fallback_exc).__name__,
                    fallback_exc,
                )

    elif use_narrative_v2 and model_gateway is not None and db_path is not None:
        backend = model_gateway.select_backend("write") if hasattr(model_gateway, "select_backend") else None
        backend_kind = getattr(backend, "kind", "") if backend is not None else ""
        backend_model = getattr(backend, "model", "") if backend is not None else ""
        model_name = backend_model or backend_kind or getattr(model_gateway, "active_profile", "")

        v2_body = render_v2_narrative(
            work_blocks,
            model_gateway=model_gateway,
            db_path=db_path,
            date_str=date_str,
        )
        if v2_body:
            narrative_body = v2_body
            llm_used = not v2_body.startswith("今日证据不足")

    elif model_gateway is not None:
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
    body_parts = [
        narrative_body.strip(),
        render_daily_decisions(decisions),
    ]
    if skeleton_rendered:
        body_parts.append(_render_event_cards(normalized))
    body = "\n\n".join(part for part in body_parts if part).strip()

    return DailyDraft(
        body=body,
        llm_used=llm_used,
        event_count=len(normalized),
        model_name=model_name,
        prompt_hash=prompt_hash,
    )
