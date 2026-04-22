from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from typing import Any, Iterable

from keypulse.pipeline.feedback import FeedbackEvent, summarize_feedback_events
from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.themes import read_theme_profile, theme_summary_patch


@dataclass(frozen=True)
class ThemeSummary:
    body: str
    llm_used: bool
    topic_counts: dict[str, int]
    theme_name: str = "general"
    version: int = 1


def _group_for_item(item: dict[str, Any]) -> str:
    text = " ".join(
        str(part or "").lower()
        for part in (
            item.get("topic"),
            item.get("title"),
            item.get("body"),
        )
    )
    if any(token in text for token in ("decision", "model", "insight", "tradeoff", "reason", "strategy", "thinking")):
        return "cognitive upgrade"
    if any(token in text for token in ("plan", "process", "workflow", "policy", "budget", "test", "design", "architecture", "feedback")):
        return "methodology"
    if any(token in text for token in ("install", "deploy", "run", "sync", "cli", "command", "bug", "fix", "issue")):
        return "execution experience"
    return "execution experience"


def _deterministic_summary(items: list[dict[str, Any]], profile_name: str, version: int) -> str:
    grouped: dict[str, list[str]] = {
        "cognitive upgrade": [],
        "methodology": [],
        "execution experience": [],
    }
    for item in items:
        title = str(item.get("title") or item.get("topic") or "untitled")
        grouped[_group_for_item(item)].append(title)

    lines = [f"# {profile_name} v{version}", ""]
    for group_name in ("cognitive upgrade", "methodology", "execution experience"):
        titles = grouped[group_name]
        if not titles:
            continue
        lines.append(f"## {group_name}")
        for title in titles[:5]:
            lines.append(f"- {title}")
        lines.append("")
    return "\n".join(lines).strip()


def build_theme_summary(
    items: list[dict[str, Any]],
    *,
    model_gateway: ModelGateway | None = None,
    theme_state_path: str | None = None,
    prompt_patch: str = "",
    feedback_events: Iterable[FeedbackEvent] | None = None,
) -> ThemeSummary:
    counts = Counter(str(item.get("topic") or "untitled") for item in items)
    profile = read_theme_profile(theme_state_path)
    compact_patch = "\n".join(
        part for part in (
            theme_summary_patch(profile),
            summarize_feedback_events(feedback_events or []),
            prompt_patch.strip(),
        )
        if part
    )

    if model_gateway is not None:
        backend = model_gateway.select_backend("aggregate")
        if backend.kind != "disabled":
            evidence_lines = [f"{item.get('topic') or 'untitled'}: {item.get('title') or ''}" for item in items]
            body = model_gateway.summarize_theme(
                profile.theme_name,
                evidence_lines,
                prompt_patch=compact_patch,
            )
            return ThemeSummary(
                body=body,
                llm_used=True,
                topic_counts=dict(counts),
                theme_name=profile.theme_name,
                version=profile.version,
            )

    return ThemeSummary(
        body=_deterministic_summary(items, profile.theme_name, profile.version),
        llm_used=False,
        topic_counts=dict(counts),
        theme_name=profile.theme_name,
        version=profile.version,
    )
