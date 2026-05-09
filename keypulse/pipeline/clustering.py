from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit


_TOKEN_RE = re.compile(r"[a-z0-9_./:-]{2,}")


@dataclass(frozen=True)
class EventFeature:
    event_id: str
    ts: datetime
    h1_entities: frozenset[str]
    session_id: str | None
    app_window: str | None
    chrome_tab: str | None
    keywords: frozenset[str]


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        normalized = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_url_without_query(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.split("?", 1)[0].split("#", 1)[0]
    if not parsed.scheme or not parsed.netloc:
        return raw.split("?", 1)[0].split("#", 1)[0]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))


def _metadata_dict(event: Mapping[str, Any]) -> dict[str, Any]:
    raw = event.get("metadata_json")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _to_token_set(values: list[str], prefix: str) -> set[str]:
    result: set[str] = set()
    for value in values:
        normalized = str(value or "").strip().lower()
        if normalized:
            result.add(f"{prefix}:{normalized}")
    return result


def _extract_keywords(content: str, entities: set[str]) -> set[str]:
    keywords = set(_TOKEN_RE.findall(content.lower()))
    for entity in entities:
        _, _, tail = entity.partition(":")
        keywords.update(_TOKEN_RE.findall(tail.lower()))
    return {item for item in keywords if len(item) >= 2}


def _extract_event_feature(event: Mapping[str, Any]) -> EventFeature:
    metadata = _metadata_dict(event)
    entities_raw = metadata.get("entities")
    entities = dict(entities_raw) if isinstance(entities_raw, dict) else {}

    commit_hash = str(entities.get("commit_hash") or "").strip()
    file_paths = [str(item) for item in (entities.get("file_paths") or []) if str(item).strip()]
    urls = [_normalize_url_without_query(str(item)) for item in (entities.get("urls") or []) if str(item).strip()]
    named_entities = [str(item) for item in (entities.get("named_entities") or []) if str(item).strip()]

    h1 = set()
    if commit_hash:
        h1.add(f"commit:{commit_hash.lower()}")
    h1.update(_to_token_set(file_paths, "file"))
    h1.update(_to_token_set(urls, "url"))
    h1.update(_to_token_set(named_entities, "ne"))

    session_id = str(entities.get("session_id") or event.get("session_id") or "").strip() or None
    app = str(event.get("app_name") or metadata.get("app_name") or "").strip().lower()
    window = str(event.get("window_title") or metadata.get("window_title") or "").strip().lower()
    app_window = f"{app}|{window}" if app and window else None
    chrome_tab = None
    if urls:
        chrome_tab = urls[0]
    else:
        metadata_url = _normalize_url_without_query(str(metadata.get("url") or ""))
        if metadata_url:
            chrome_tab = metadata_url

    content = " ".join(
        [
            str(event.get("content_text") or ""),
            str(event.get("window_title") or ""),
            str(metadata.get("artifact") or ""),
        ]
    ).strip()
    keywords = _extract_keywords(content, h1)

    event_id = str(event.get("event_id") or event.get("id") or "").strip()
    if not event_id:
        raise ValueError("event must include id/event_id")

    ts_value = event.get("timestamp") or event.get("ts_start") or event.get("time")
    if not ts_value:
        raise ValueError(f"event {event_id} missing timestamp/ts_start/time")

    return EventFeature(
        event_id=event_id,
        ts=_parse_ts(ts_value),
        h1_entities=frozenset(h1),
        session_id=session_id,
        app_window=app_window,
        chrome_tab=chrome_tab,
        keywords=frozenset(keywords),
    )


def _h1_match(left: EventFeature, right: EventFeature) -> bool:
    return bool(left.h1_entities & right.h1_entities)


def _h2_match(left: EventFeature, right: EventFeature) -> bool:
    if left.session_id and right.session_id and left.session_id == right.session_id:
        return True
    if left.app_window and right.app_window and left.app_window == right.app_window:
        return True
    if left.chrome_tab and right.chrome_tab and left.chrome_tab == right.chrome_tab:
        return True
    return False


def _strong_evidence_match(left: EventFeature, right: EventFeature) -> bool:
    if _h1_match(left, right):
        return True
    return bool(left.session_id and right.session_id and left.session_id == right.session_id)


def build_evidence_graph(events: list[Mapping[str, Any]]) -> dict[str, set[str]]:
    """
    Build hard-evidence adjacency graph.

    Rules:
    - H1: shared entity id (commit/file/url/named entity)
    - H2: same capture context (session / app+window / chrome tab)
    - H3: time proximity
      - <5 min: always connect
      - 5-30 min: require H1 or H2 boost
      - >30 min: connect only with strong evidence (H1 or same session)
    """

    features = [_extract_event_feature(event) for event in events]
    graph: dict[str, set[str]] = {feature.event_id: set() for feature in features}

    for idx, left in enumerate(features):
        for right in features[idx + 1 :]:
            h1_hit = _h1_match(left, right)
            h2_hit = _h2_match(left, right)
            delta_min = abs((right.ts - left.ts).total_seconds()) / 60.0

            should_link = False
            if delta_min < 5:
                should_link = True
            elif 5 <= delta_min <= 30:
                should_link = h1_hit or h2_hit
            else:
                should_link = _strong_evidence_match(left, right)

            # Strong H1 should connect regardless of time bucket.
            if h1_hit:
                should_link = True

            if should_link:
                graph[left.event_id].add(right.event_id)
                graph[right.event_id].add(left.event_id)

    return graph


def connected_components(graph: Mapping[str, set[str]]) -> list[set[str]]:
    visited: set[str] = set()
    components: list[set[str]] = []

    for node in graph:
        if node in visited:
            continue
        stack = [node]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            for neighbor in graph.get(current, set()):
                if neighbor not in visited:
                    stack.append(neighbor)
        components.append(component)

    return components


def build_feature_index(events: list[Mapping[str, Any]]) -> dict[str, dict[str, set[str]]]:
    index: dict[str, dict[str, set[str]]] = {}
    for event in events:
        feature = _extract_event_feature(event)
        index[feature.event_id] = {
            "keywords": set(feature.keywords),
            "entities": set(feature.h1_entities),
        }
    return index


def detect_merge_candidates(
    components: list[set[str]],
    threshold: float,
    *,
    feature_index: Mapping[str, Mapping[str, set[str]]] | None = None,
) -> list[tuple[str, str]]:
    """
    Detect cross-component merge candidates by Jaccard overlap on keywords/entities.
    """
    if threshold < 0 or threshold > 1:
        raise ValueError("threshold must be between 0 and 1")
    if not components:
        return []

    index = feature_index or {}
    signature: list[set[str]] = []
    for component in components:
        merged_keywords: set[str] = set()
        merged_entities: set[str] = set()
        for event_id in component:
            payload = index.get(event_id, {})
            merged_keywords.update(payload.get("keywords", set()))
            merged_entities.update(payload.get("entities", set()))
        signature.append(merged_keywords | merged_entities)

    candidates: list[tuple[str, str]] = []
    for i in range(len(components)):
        for j in range(i + 1, len(components)):
            left = signature[i]
            right = signature[j]
            if not left or not right:
                continue
            inter = left & right
            union = left | right
            jaccard = (len(inter) / len(union)) if union else 0.0
            if jaccard >= threshold:
                left_id = ",".join(sorted(components[i]))
                right_id = ",".join(sorted(components[j]))
                candidates.append((left_id, right_id))
    return candidates


def component_features(
    component: set[str],
    feature_index: Mapping[str, Mapping[str, set[str]]],
) -> dict[str, set[str]]:
    merged = defaultdict(set)
    for event_id in component:
        payload = feature_index.get(event_id, {})
        merged["keywords"].update(payload.get("keywords", set()))
        merged["entities"].update(payload.get("entities", set()))
    return {"keywords": set(merged["keywords"]), "entities": set(merged["entities"])}
