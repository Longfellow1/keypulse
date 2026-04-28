from __future__ import annotations

from keypulse.pipeline.entity_extractor import extract_keywords, extract_text_keywords
from keypulse.pipeline.outline_prompt import ThingOutline
from keypulse.sources.types import SemanticEvent


def assign_events(events: list[SemanticEvent], outlines: list[ThingOutline]) -> dict[str, list[SemanticEvent]]:
    """Assign each event into one outline bucket, unmatched goes to _其他."""
    if not outlines:
        return {"_其他": list(events)}
    buckets = {outline.title: [] for outline in outlines}
    buckets["_其他"] = []
    outline_keywords = [(outline.title, _keywords_from_text(f"{outline.title} {outline.summary_hint}")) for outline in outlines]
    for event in events:
        event_keywords = extract_keywords(event)
        best_title = "_其他"
        best_score: tuple[float, float] = (0.0, 0.0)
        for title, keywords in outline_keywords:
            coverage, similarity = _scores(event_keywords, keywords)
            if coverage < 0.15:
                continue
            if (similarity, coverage) > best_score:
                best_score = (similarity, coverage)
                best_title = title
        buckets[best_title].append(event)
    return buckets


def _keywords_from_text(text: str) -> set[str]:
    return extract_text_keywords(text)


def _scores(event_keywords: set[str], outline_keywords: set[str]) -> tuple[float, float]:
    event_view = _coverage_view(event_keywords)
    outline_view = _coverage_view(outline_keywords)
    if not event_view or not outline_view:
        return 0.0, 0.0
    matched_events: set[str] = set()
    weighted_similarity = 0.0
    for outline_keyword in outline_view:
        for event_keyword in event_view:
            if _keyword_match(event_keyword, outline_keyword):
                matched_events.add(event_keyword)
                weighted_similarity += float(min(16, len(outline_keyword)))
                break
    coverage = len(matched_events) / len(event_view)
    return coverage, weighted_similarity


def _coverage_view(keywords: set[str]) -> set[str]:
    return {token for token in keywords if _is_ascii_token(token) or len(token) >= 2}


def _is_ascii_token(token: str) -> bool:
    return token.isascii()


def _keyword_match(event_keyword: str, outline_keyword: str) -> bool:
    return event_keyword == outline_keyword or event_keyword in outline_keyword or outline_keyword in event_keyword
