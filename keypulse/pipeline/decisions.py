from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from keypulse.pipeline.feedback import FeedbackEvent
from keypulse.pipeline.narrative import WorkBlock


@dataclass(frozen=True)
class DecisionItem:
    kind: str
    title: str
    reason: str
    command: str
    target: str
    priority: int
    evidence: str = ""


def _first_candidate(block: WorkBlock) -> dict[str, Any]:
    return block.key_candidates[0] if block.key_candidates else {}


def _candidate_source(block: WorkBlock) -> str:
    return str(_first_candidate(block).get("source") or "")


def _candidate_title(block: WorkBlock) -> str:
    title = str(_first_candidate(block).get("title") or block.theme or "event").strip()
    return title or "event"


def _target_for_block(block: WorkBlock) -> str:
    title = block.theme.strip() or _candidate_title(block)
    return title.replace(" ", "-")


def _feedback_counts(events: Iterable[FeedbackEvent] | None) -> Counter[str]:
    counts: Counter[str] = Counter()
    for event in events or []:
        if event is None:
            continue
        target = str(event.target or "").strip()
        if target:
            counts[target] += 1
    return counts


def build_daily_decisions(
    work_blocks: Iterable[WorkBlock],
    *,
    theme_keys: set[str] | None = None,
    recent_topic_counts: Mapping[str, int] | None = None,
    feedback_events: Iterable[FeedbackEvent] | None = None,
    max_items: int = 3,
) -> list[DecisionItem]:
    blocks = [block for block in work_blocks if not block.fragment]
    if not blocks:
        return []

    theme_keys = set(theme_keys or set())
    counts = Counter()
    for block in blocks:
        counts[block.theme] += 1
    feedback_counts = _feedback_counts(feedback_events)
    decisions: list[DecisionItem] = []

    for index, block in enumerate(blocks):
        target = _target_for_block(block)
        title = _candidate_title(block)
        source = _candidate_source(block)
        count = int(recent_topic_counts.get(block.theme, 0) if recent_topic_counts else 0) + counts[block.theme]
        feedback_count = feedback_counts.get(target, 0)
        open_ended = block.continuity in {"new", "returned"} and source in {"clipboard", "window", "ax_text", "ocr_text"}

        if block.theme in theme_keys and count >= 3:
            decisions.append(
                DecisionItem(
                    kind="promote",
                    title=title,
                    reason="已立主题，继续记录",
                    command=f"keypulse pipeline feedback promote {target}",
                    target=target,
                    priority=0,
                    evidence=block.theme,
                )
            )
            continue

        if count >= 4:
            decisions.append(
                DecisionItem(
                    kind="promote",
                    title=title,
                    reason="相关证据已经够密，升级为主题",
                    command=f"keypulse pipeline feedback promote {target}",
                    target=target,
                    priority=0,
                    evidence=block.theme,
                )
            )
            continue

        if feedback_count >= 2:
            decisions.append(
                DecisionItem(
                    kind="defer",
                    title=title,
                    reason="这件事已经 defer 多次，要不要先挂起",
                    command=f"keypulse pipeline feedback defer {target}",
                    target=target,
                    priority=1,
                    evidence=block.theme,
                )
            )
            continue

        if source == "manual":
            decisions.append(
                DecisionItem(
                    kind="archive",
                    title=title,
                    reason="你主动保存，建议加标签归档",
                    command=f"keypulse pipeline feedback archive {target}",
                    target=target,
                    priority=2,
                    evidence=block.theme,
                )
            )
            continue

        if open_ended or (index == len(blocks) - 1 and block.duration_sec < 300):
            decisions.append(
                DecisionItem(
                    kind="defer",
                    title=title,
                    reason="今天没收束，明日继续看",
                    command=f"keypulse pipeline feedback defer {target}",
                    target=target,
                    priority=1,
                    evidence=block.theme,
                )
            )
            continue

        if block.duration_sec < 300:
            decisions.append(
                DecisionItem(
                    kind="archive",
                    title=title,
                    reason="一次性输入，建议加标签归档",
                    command=f"keypulse pipeline feedback archive {target}",
                    target=target,
                    priority=2,
                    evidence=block.theme,
                )
            )

    unique: dict[str, DecisionItem] = {}
    for item in sorted(decisions, key=lambda decision: (decision.priority, decision.title.lower(), decision.target)):
        unique.setdefault(item.target, item)
        if len(unique) >= max_items:
            break
    return list(unique.values())


def render_daily_decisions(decisions: Iterable[DecisionItem | dict[str, Any]], *, include_heading: bool = True) -> str:
    items = [
        DecisionItem(
            kind=str(decision.get("kind") or "defer"),
            title=str(decision.get("title") or decision.get("target") or "event"),
            reason=str(decision.get("reason") or ""),
            command=str(decision.get("command") or ""),
            target=str(decision.get("target") or decision.get("title") or "event"),
            priority=int(decision.get("priority") or 0),
            evidence=str(decision.get("evidence") or ""),
        )
        if isinstance(decision, dict)
        else decision
        for decision in decisions
    ]
    lines = ["## 需要你决定", ""] if include_heading else []
    if not items:
        lines.append("- 今天没有必须拍板的事")
        return "\n".join(lines).strip()

    for item in items:
        lines.extend(
            [
                f"- {item.title}：{item.reason}",
                f"  - 命令：{item.command}",
            ]
        )
    return "\n".join(lines).strip()
