from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Mapping


SOURCE_INTAKE_WEIGHTS: dict[str, float] = {
    "markdown_vault": 1.0,
    "clipboard": 1.0,
    "manual": 1.0,
    "claude_code": 0.95,
    "codex_cli": 0.95,
    "ax_text": 0.85,
    "window": 0.85,
    "browser_url": 1.0,
    "keyboard_chunk": 0.7,
    "knowledgec": 0.6,
    "zsh_history": 0.6,
    "idle": 0.4,
}
DEFAULT_INTAKE_WEIGHT = 0.7
SOURCE_MIN_QUOTA_BASE = 5
SOURCE_MIN_QUOTA_FOR_IDLE = 0

_CODE_SYMBOL_RE = re.compile(r"\b(?:def|function|class|const|let|var|fn|func)\s+([A-Za-z_][A-Za-z0-9_]*)")
_JACCARD_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_HAS_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PATH_OR_URL_RE = re.compile(r"(?:https?://|www\.|[A-Za-z]:[\\/]|/[^/\s]+/|\\\\[^\\\s]+[\\/])")
_MENU_SPLIT_RE = re.compile(r"[|>›]")


def _metadata_dict(event: Mapping[str, Any]) -> dict[str, Any]:
    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        return dict(metadata)
    raw = event.get("metadata_json")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _metadata_entities(event: Mapping[str, Any]) -> dict[str, Any]:
    entities = _metadata_dict(event).get("entities")
    return dict(entities) if isinstance(entities, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def is_app_chrome(event: Mapping[str, Any]) -> bool:
    content = str(event.get("content_text") or "").strip()
    if not content:
        return False
    window_title = str(event.get("window_title") or "").strip()
    app_name = str(event.get("app_name") or "").strip()
    if window_title and content == window_title:
        return True
    if window_title and len(content) >= 4 and content in window_title:
        return True
    if window_title and len(content) >= 4 and window_title.endswith(content):
        return True
    return bool(app_name and content == app_name)


def is_loading_title(window_title: str) -> bool:
    text = str(window_title or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if text in {"…", "..."}:
        return True
    if text in {"新建标签页", "请稍候"}:
        return True
    if lowered in {"untitled", "loading"}:
        return True
    return "加载中" in text or "loading" in lowered


def is_machine_output(content: str) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return (
        text.startswith("[已恢复")
        or lowered.startswith("redacted-")
        or lowered.startswith("export ")
        or lowered.startswith("export-")
        or lowered.startswith("data:")
        or lowered.startswith("<!doctype")
        or lowered.startswith("<html")
    )


def is_menu_chrome(content: str) -> bool:
    text = str(content or "").strip()
    if not text or len(text) > 20:
        return False
    split_count = len(_MENU_SPLIT_RE.findall(text))
    letters = [char for char in text if char.isalpha()]
    all_caps = bool(letters) and all(char.isupper() for char in letters)
    return all_caps or split_count >= 3


def is_input_fragment(content: str) -> bool:
    text = str(content or "").strip()
    if len(text) <= 12:
        return False
    if " " in text:
        return False
    if _HAS_CJK_RE.search(text):
        return False
    if _PATH_OR_URL_RE.search(text):
        return False
    if re.search(r"[.?!。！？]", text):
        return False
    if is_code_snippet(text):
        return False
    if not all(ord(char) < 128 for char in text):
        return False
    letters = sum(1 for char in text if char.isalpha())
    if letters / max(len(text), 1) < 0.7:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9,_:;'\-\\/+]+", text))


def is_empty_event(event: Mapping[str, Any]) -> bool:
    source = str(event.get("source") or "").strip().lower()
    content = str(event.get("content_text") or "").strip()
    window_title = str(event.get("window_title") or "").strip()
    entities = _metadata_entities(event)
    urls = _string_list(entities.get("urls"))
    file_paths = _string_list(entities.get("file_paths"))

    if not content and not window_title and not urls and not file_paths:
        if source != "idle":
            return True

    if source != "idle":
        return False

    metadata = _metadata_dict(event)
    if urls or file_paths:
        return False
    if content or window_title:
        return False
    meaningful = {k: v for k, v in metadata.items() if k not in {"entities", "window_title", "app_name"} and v not in (None, "", [], {})}
    entity_keys = {k: v for k, v in entities.items() if v not in (None, "", [], {}) and k not in {"session_id"}}
    return not meaningful and not entity_keys


def is_code_snippet(content: str) -> bool:
    text = str(content or "")

    features = 0
    lowered = text.lower()
    if any(token in lowered for token in ("def ", "function ", "class ", "import ", "=>", "const ", "let ")):
        features += 1

    lines = text.splitlines()
    has_indented = any(line.startswith(("    ", "\t")) for line in lines)
    has_terminator = any(line.rstrip().endswith((":","{",";")) for line in lines)
    if has_indented and has_terminator:
        features += 1

    if re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", text):
        features += 1

    identifiers = re.findall(r"\b(?:[A-Za-z]+_[A-Za-z0-9_]+|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)\b", text)
    if len(identifiers) >= 3:
        features += 1

    if len(text.strip()) > 80:
        return features >= 2
    return features >= 3


def extract_code_symbols(content: str, max_n: int = 5) -> list[str]:
    if max_n <= 0:
        return []
    found = _CODE_SYMBOL_RE.findall(content or "")
    symbols: list[str] = []
    seen: set[str] = set()
    for symbol in found:
        if symbol in seen:
            continue
        symbols.append(symbol)
        seen.add(symbol)
        if len(symbols) >= max_n:
            break
    return symbols


def prune_code_snippet(event: Mapping[str, Any], source: str) -> dict[str, Any]:
    out = dict(event)
    text = str(out.get("content_text") or "")
    limit = 120 if source in {"claude_code", "codex_cli"} else 60
    if len(text) > limit:
        out["content_text"] = text[:limit]

    metadata = _metadata_dict(out)
    metadata["code_symbols"] = extract_code_symbols(text, max_n=5)
    out["metadata"] = metadata
    if "metadata_json" in out:
        out["metadata_json"] = json.dumps(metadata, ensure_ascii=False)
    return out


def clean_events_semantic(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for event in events:
        source = str(event.get("source") or "").strip().lower()
        text = str(event.get("content_text") or "").strip()
        if is_empty_event(event):
            continue
        if is_app_chrome(event):
            continue
        if is_machine_output(text):
            continue
        if is_menu_chrome(text):
            continue
        if is_input_fragment(text):
            continue

        normalized = dict(event)
        if is_loading_title(str(normalized.get("window_title") or "")):
            normalized["window_title"] = ""
        if is_code_snippet(text):
            normalized = prune_code_snippet(normalized, source)
        cleaned.append(normalized)
    return cleaned


def _source_counts(events: list[dict[str, Any]] | Mapping[str, int]) -> dict[str, int]:
    if isinstance(events, Mapping):
        counts = {str(source).strip().lower(): int(count) for source, count in events.items()}
        return {source: count for source, count in counts.items() if source and count > 0}
    counter = Counter()
    for event in events:
        source = str(event.get("source") or "").strip().lower()
        if not source:
            source = "unknown"
        counter[source] += 1
    return dict(counter)


def compute_source_quotas(events: list[dict[str, Any]] | Mapping[str, int], limit: int) -> dict[str, int]:
    if limit <= 0:
        return {}

    source_counts = _source_counts(events)
    if not source_counts:
        return {}

    non_idle_sources = [source for source in source_counts if source != "idle"]
    n_non_idle = len(non_idle_sources)
    if n_non_idle == 0:
        return {"idle": min(source_counts.get("idle", 0), limit)}

    if n_non_idle * 5 <= limit:
        min_quota = 5
    elif n_non_idle * 4 <= limit:
        min_quota = 4
    elif n_non_idle * 3 <= limit:
        min_quota = 3
    else:
        min_quota = max(1, limit // n_non_idle)

    quotas: dict[str, int] = {}
    reserved = 0
    for source, count in source_counts.items():
        base = min_quota if source != "idle" else SOURCE_MIN_QUOTA_FOR_IDLE
        base = min(base, count)
        quotas[source] = base
        reserved += base

    remaining = max(0, limit - reserved)
    if remaining <= 0:
        return quotas

    weighted_share: dict[str, float] = {
        source: SOURCE_INTAKE_WEIGHTS.get(source, DEFAULT_INTAKE_WEIGHT) * count for source, count in source_counts.items()
    }
    total_weighted = sum(weighted_share.values())
    if total_weighted <= 0:
        return quotas

    desired_extra: dict[str, float] = {}
    for source, share in weighted_share.items():
        desired_extra[source] = remaining * (share / total_weighted)

    for source in source_counts:
        capacity = source_counts[source] - quotas[source]
        if capacity <= 0:
            continue
        extra = min(capacity, max(0, int(round(desired_extra[source]))))
        quotas[source] += extra

    total = sum(quotas.values())
    if total < limit:
        need = limit - total
        order = sorted(
            source_counts,
            key=lambda source: (
                desired_extra.get(source, 0.0) - int(desired_extra.get(source, 0.0)),
                SOURCE_INTAKE_WEIGHTS.get(source, DEFAULT_INTAKE_WEIGHT),
                source_counts[source],
            ),
            reverse=True,
        )
        for source in order:
            if need <= 0:
                break
            capacity = source_counts[source] - quotas[source]
            if capacity <= 0:
                continue
            add = min(capacity, need)
            quotas[source] += add
            need -= add
    elif total > limit:
        over = total - limit
        order = sorted(
            source_counts,
            key=lambda source: (
                SOURCE_INTAKE_WEIGHTS.get(source, DEFAULT_INTAKE_WEIGHT),
                source_counts[source],
            ),
        )
        for source in order:
            if over <= 0:
                break
            floor = min_quota if source != "idle" else SOURCE_MIN_QUOTA_FOR_IDLE
            floor = min(floor, source_counts[source])
            removable = max(0, quotas[source] - floor)
            if removable <= 0:
                continue
            take = min(removable, over)
            quotas[source] -= take
            over -= take

    return quotas


def _parse_ts(ts_iso: str) -> datetime | None:
    text = str(ts_iso or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tokenize_for_jaccard(text: str) -> set[str]:
    return {token.lower() for token in _JACCARD_TOKEN_RE.findall(str(text or ""))}


def _jaccard_similarity(left: str, right: str) -> float:
    left_tokens = _tokenize_for_jaccard(left)
    right_tokens = _tokenize_for_jaccard(right)
    if not left_tokens and not right_tokens:
        return 1.0
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def _entity_list(event: Mapping[str, Any], key: str) -> list[str]:
    return _string_list(_metadata_entities(event).get(key))


def _entities_differ(prev: Mapping[str, Any], current: Mapping[str, Any], key: str) -> bool:
    prev_values = _entity_list(prev, key)
    current_values = _entity_list(current, key)
    return sorted(set(prev_values)) != sorted(set(current_values))


def _event_rank_score(event: Mapping[str, Any]) -> float:
    supplied = event.get("_flagship_score")
    if isinstance(supplied, (int, float)):
        return float(supplied)

    score = float(event.get("semantic_weight") or 0.0)
    text = " ".join(
        str(part or "").strip() for part in (event.get("content_text"), event.get("window_title"), event.get("app_name")) if str(part or "").strip()
    )
    if _HAS_CJK_RE.search(text):
        score += 0.3
    content = str(event.get("content_text") or "").strip()
    if 12 <= len(content) <= 260:
        score += 0.15
    window = str(event.get("window_title") or "")
    if window and (" - " in window or " — " in window or " – " in window):
        score += 0.4
    return round(score, 4)


def collapse_self_repeat(events_bucket: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(events_bucket) <= 1:
        return list(events_bucket)

    ordered = sorted(events_bucket, key=lambda event: str(event.get("ts_start") or ""))
    kept: list[dict[str, Any]] = []
    for event in ordered:
        current = dict(event)
        if not kept:
            kept.append(current)
            continue

        prev = kept[-1]
        if _entities_differ(prev, current, "file_paths") or _entities_differ(prev, current, "urls"):
            kept.append(current)
            continue

        prev_ts = _parse_ts(str(prev.get("ts_start") or ""))
        curr_ts = _parse_ts(str(current.get("ts_start") or ""))
        if prev_ts is None or curr_ts is None:
            kept.append(current)
            continue
        gap_seconds = int((curr_ts - prev_ts).total_seconds())
        if gap_seconds < 0 or gap_seconds > 300:
            kept.append(current)
            continue

        threshold = 0.65 if gap_seconds <= 60 else 0.80
        sim = _jaccard_similarity(str(prev.get("content_text") or ""), str(current.get("content_text") or ""))
        if sim >= threshold:
            merged = current if len(str(current.get("content_text") or "")) > len(str(prev.get("content_text") or "")) else prev
            merged_copy = dict(merged)
            merged_copy["dwell_seconds"] = int(prev.get("dwell_seconds") or 0) + int(current.get("dwell_seconds") or 0) + gap_seconds
            kept[-1] = merged_copy
        else:
            kept.append(current)
    return kept


def cap_events_by_source(events: list[dict[str, Any]], *, limit: int = 60) -> tuple[list[dict[str, Any]], bool]:
    if limit <= 0:
        return [], bool(events)

    cleaned = clean_events_semantic(events)
    if not cleaned:
        return [], bool(events)

    from keypulse.pipeline.daily_strategy import extract_work_unit

    for index, event in enumerate(cleaned):
        event["_cap_index"] = index
        event["_cap_score"] = _event_rank_score(event)

    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in cleaned:
        source = str(event.get("source") or "").strip().lower() or "unknown"
        by_source[source].append(event)

    source_deduped: dict[str, list[dict[str, Any]]] = {}
    for source, bucket in by_source.items():
        by_wu: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in bucket:
            by_wu[extract_work_unit(event)].append(event)
        collapsed_bucket: list[dict[str, Any]] = []
        for wu_bucket in by_wu.values():
            collapsed_bucket.extend(collapse_self_repeat(wu_bucket))
        source_deduped[source] = collapsed_bucket

    deduped_events = [event for bucket in source_deduped.values() for event in bucket]
    if len(deduped_events) <= limit:
        out = sorted(deduped_events, key=lambda event: int(event.get("_cap_index") or 0))
        for event in out:
            event.pop("_cap_index", None)
            event.pop("_cap_score", None)
            event.pop("_flagship_score", None)
        return out, len(out) < len(events)

    quotas = compute_source_quotas(deduped_events, limit)
    selected_ids: set[int] = set()

    for source, bucket in source_deduped.items():
        quota = quotas.get(source, 0)
        if quota <= 0:
            continue
        by_wu: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in bucket:
            by_wu[extract_work_unit(event)].append(event)

        representatives: list[dict[str, Any]] = []
        for wu_events in by_wu.values():
            representatives.append(
                max(
                    wu_events,
                    key=lambda event: (
                        float(event.get("_cap_score") or 0.0),
                        -int(event.get("_cap_index") or 0),
                    ),
                )
            )

        reps_sorted = sorted(
            representatives,
            key=lambda event: (
                float(event.get("_cap_score") or 0.0),
                -int(event.get("_cap_index") or 0),
            ),
            reverse=True,
        )
        for event in reps_sorted[:quota]:
            selected_ids.add(id(event))

        if len(representatives) < quota:
            left = quota - len(representatives)
            remainder = [event for event in bucket if id(event) not in selected_ids]
            remainder_sorted = sorted(
                remainder,
                key=lambda event: (
                    float(event.get("_cap_score") or 0.0),
                    -int(event.get("_cap_index") or 0),
                ),
                reverse=True,
            )
            for event in remainder_sorted[:left]:
                selected_ids.add(id(event))

    selected = [event for event in deduped_events if id(event) in selected_ids]
    if len(selected) > limit:
        selected = sorted(
            selected,
            key=lambda event: (
                float(event.get("_cap_score") or 0.0),
                -int(event.get("_cap_index") or 0),
            ),
            reverse=True,
        )[:limit]
    elif len(selected) < limit:
        remainder = [event for event in deduped_events if id(event) not in selected_ids]
        remainder_sorted = sorted(
            remainder,
            key=lambda event: (
                float(event.get("_cap_score") or 0.0),
                -int(event.get("_cap_index") or 0),
            ),
            reverse=True,
        )
        selected.extend(remainder_sorted[: max(0, limit - len(selected))])

    out = sorted(selected, key=lambda event: int(event.get("_cap_index") or 0))
    for event in out:
        event.pop("_cap_index", None)
        event.pop("_cap_score", None)
        event.pop("_flagship_score", None)
    return out[:limit], len(out) < len(events)
