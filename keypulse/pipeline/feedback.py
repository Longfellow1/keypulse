from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from keypulse.pipeline.themes import read_theme_profile, record_theme_refine


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class FeedbackEvent:
    kind: str
    target: str
    note: str
    created_at: str = field(default_factory=_now)


def append_feedback_event(path: str | Path, event: FeedbackEvent) -> None:
    feedback_path = Path(path).expanduser()
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    with feedback_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


def read_feedback_events(path: str | Path) -> list[FeedbackEvent]:
    feedback_path = Path(path).expanduser()
    if not feedback_path.exists():
        return []
    events: list[FeedbackEvent] = []
    for line in feedback_path.read_text().splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        events.append(
            FeedbackEvent(
                kind=data["kind"],
                target=data["target"],
                note=data["note"],
                created_at=data.get("created_at", _now()),
            )
        )
    return events


def summarize_feedback_events(events: Iterable[FeedbackEvent], limit: int = 6) -> str:
    feedback_events = [event for event in events if event is not None]
    if not feedback_events:
        return "无历史反馈"

    first_seen: dict[tuple[str, str], int] = {}
    counts: Counter[tuple[str, str]] = Counter()
    for index, event in enumerate(feedback_events):
        key = (event.kind.strip(), event.target.strip())
        if not key[0] or not key[1]:
            continue
        counts[key] += 1
        first_seen.setdefault(key, index)

    if not counts:
        return "无历史反馈"

    ordered = sorted(
        counts.items(),
        key=lambda item: (-item[1], first_seen[item[0]], item[0][0], item[0][1]),
    )
    parts = [f"{kind} {target} x{count}" for (kind, target), count in ordered[:limit]]
    if len(ordered) > limit:
        parts.append(f"…另有 {len(ordered) - limit} 条")
    return "; ".join(parts)


def record_theme_feedback(path: str | Path | None, theme_name: str, instruction: str) -> dict[str, object]:
    profile = record_theme_refine(path, theme_name=theme_name, instruction=instruction)
    return {
        "theme_name": profile.theme_name,
        "version": profile.version,
        "instructions": list(profile.instructions),
        "updated_at": profile.updated_at,
    }


def current_theme_profile(path: str | Path | None = None) -> dict[str, object]:
    profile = read_theme_profile(path)
    return {
        "theme_name": profile.theme_name,
        "version": profile.version,
        "instructions": list(profile.instructions),
        "updated_at": profile.updated_at,
    }
