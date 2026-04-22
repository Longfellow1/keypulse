from __future__ import annotations

from typing import Any


def _score(item: dict[str, Any]) -> float:
    try:
        return float(item.get("score") or 0.0)
    except Exception:
        return 0.0


def _text(item: dict[str, Any]) -> str:
    parts = [
        str(item.get("title") or ""),
        str(item.get("body") or ""),
        str(item.get("content_text") or ""),
        str(item.get("window_title") or ""),
    ]
    return " ".join(part for part in parts if part).strip()


def _classify(item: dict[str, Any]) -> tuple[str, float]:
    text = _text(item).lower()
    score = max(_score(item), 0.0)
    category = "evidence"
    if "?" in text or text.startswith(("how ", "what ", "why ", "should ", "could ")):
        category = "question"
        score += 0.2
    elif any(token in text for token in ("because", "learned", "realized", "insight", "pattern", "therefore", "decision")):
        category = "insight"
        score += 0.15
    elif any(token in text for token in ("evidence", "quote", "link", "see ", "example", "source")) or item.get("source") in {"clipboard", "browser"}:
        category = "evidence"
        score += 0.1
    else:
        category = "insight" if item.get("source") == "manual" else "evidence"
    return category, min(score if score else 0.5, 1.0)


def select_mining_candidates(inputs: Any, items: list[dict[str, Any]], llm_budget_remaining: int) -> list[dict[str, Any]]:
    if llm_budget_remaining <= 0:
        return []

    ranked = sorted(
        (item for item in items if _score(item) >= 0.75),
        key=lambda item: (-_score(item), str(item.get("title") or "").lower()),
    )

    candidate_cap = int(getattr(inputs, "candidate_count", 0) or 0)
    max_items = min(max(llm_budget_remaining, 0), candidate_cap)
    return ranked[:max_items]


def mine_candidate_events(items: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    ranked = []
    for item in items:
        category, score = _classify(item)
        ranked.append(
            {
                **item,
                "candidate_type": category,
                "score": max(score, _score(item)),
            }
        )

    return sorted(
        ranked,
        key=lambda item: (
            -_score(item),
            item.get("candidate_type") != "question",
            str(item.get("title") or "").lower(),
        ),
    )[:limit]
