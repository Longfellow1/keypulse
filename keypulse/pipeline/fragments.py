from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from keypulse.pipeline.normalize import canonicalize_app, strip_slug_tail, title_to_object_hint


@dataclass
class SemanticFragment:
    ts: datetime
    app: str
    verb: str
    object_hint: str
    sample: str
    weight: float
    source_id: int


@dataclass
class EvidenceSlice:
    ts_start: datetime
    ts_end: datetime
    app: str
    verbs: list[str]
    object_hints: list[str]
    samples: list[str]
    fragment_count: int
    weight_sum: float
    source_ids: list[int]


_PRIVACY_KEYWORDS = ("password", "passwd", "密码", "验证码", "verification code", "otp", "2fa", "token")

# L3: login context keywords checked against window_title and app_name
_L3_BLOCKED_KEYWORDS = (
    "loginwindow", "password", "verification", "登录", "验证码",
    "1password", "keychain", "lastpass", "bitwarden",
    "sign in", "sign-in", "signin",
)

# L3: if app_name matches but content_text is this long, trust content instead
_L3_CONTENT_OVERRIDE_LEN = 20

# L1 thresholds
_L1_MIN_LEN = 3
_L1_WHITELIST_LEN = 5
_L1_SHORT_WHITELIST = frozenset({"y", "n", "ok", "yes", "no", "嗯", "好", "对", "是", "不"})

# L2 threshold
_L2_MIN_ENTROPY = 3.0

# L4 thresholds
_L4_MAX_AVG_DISTANCE = 2.0
_L4_MAX_TOTAL_LEN = 15

# L5: language-neutral "unbroken run" filter — catches IME intermediate state,
# tokens/hashes, slugs, anything lacking word boundaries.
_L5_MIN_LEN = 12
_L5_BOUNDARY_CHARS = frozenset(" \t\n\r_.()[]{};:'\"!?，。；：「」『』、")

_EVENT_TYPE_MAPPING = {
    "keyboard_chunk_capture": ("type", 1.0),
    "keyboard_chunk": ("type", 1.0),
    "ax_text_capture": ("type", 0.8),
    "ax_text": ("type", 0.8),
    "clipboard_copy": ("paste", 0.9),
    "clipboard": ("paste", 0.9),
    "window_focus": ("switch", 0.3),
    "window_focus_session": ("switch", 0.3),
    "window_title_changed": ("switch", 0.3),
    "ocr_text_capture": ("view", 0.5),
    "ocr": ("view", 0.5),
}


def _contains_privacy_keyword(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in _PRIVACY_KEYWORDS)


def _is_login_context(window_title: str, app_name: str, content_text: str = "") -> bool:
    window_lower = (window_title or "").lower()
    if any(kw in window_lower for kw in _L3_BLOCKED_KEYWORDS):
        return True
    app_lower = (app_name or "").lower()
    if any(kw in app_lower for kw in _L3_BLOCKED_KEYWORDS):
        effective_content = content_text or ""
        content_clean = effective_content.strip() if isinstance(effective_content, str) else ""
        return len(content_clean) < _L3_CONTENT_OVERRIDE_LEN
    return False


def _shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    total = len(text)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _passes_l1(sample: str, verb: str) -> bool:
    """Return False if sample should be dropped by L1 short-input filter."""
    if verb not in ("type", "paste"):
        return True
    length = len(sample)
    if length < _L1_WHITELIST_LEN:
        # Short samples: only keep if in whitelist
        return sample.strip().lower() in _L1_SHORT_WHITELIST
    return True


def _passes_l2(sample: str, verb: str) -> bool:
    """Return False if sample should be dropped by L2 entropy filter."""
    if verb not in ("type", "paste"):
        return True
    if len(sample) <= 5:  # short samples handled by L1; skip entropy check
        return True
    return _shannon_entropy(sample) >= _L2_MIN_ENTROPY


def _looks_like_unbroken_run(sample: str) -> bool:
    """Language-neutral noise filter: drops samples whose longest run between
    word/code boundaries is >= _L5_MIN_LEN AND that run looks like a flat
    lowercase blob (not a camelCase identifier). Catches IME intermediate
    state, tokens, hashes, slugs. Skips: text with CJK chars."""
    if not sample:
        return False
    s = sample.strip()
    if len(s) < _L5_MIN_LEN:
        return False
    # CJK / non-ASCII letters present → real prose in another script, keep
    if any(ord(c) > 127 and c.isalpha() for c in s):
        return False
    # Find the longest segment between boundary chars
    longest = ""
    current = []
    for c in s:
        if c in _L5_BOUNDARY_CHARS:
            if len(current) > len(longest):
                longest = "".join(current)
            current = []
        else:
            current.append(c)
    if len(current) > len(longest):
        longest = "".join(current)
    if len(longest) < _L5_MIN_LEN:
        return False
    # camelCase: ≥2 occurrences of uppercase-followed-by-lowercase → real code
    camelcase_signals = sum(
        1 for i in range(len(longest) - 1)
        if longest[i].isupper() and longest[i + 1].islower()
    )
    if camelcase_signals >= 2:
        return False
    return True


def _levenshtein(a: str, b: str) -> int:
    """Simple DP Levenshtein distance."""
    m, n = len(a), len(b)
    if m < n:
        a, b, m, n = b, a, n, m
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        curr = [i] + [0] * n
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[n]


def _extract_sample_from_payload(payload: Any, max_len: int = 80) -> str:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return payload[:max_len]

    if isinstance(payload, dict):
        text = payload.get("text", "") or ""
        return str(text)[:max_len]

    if isinstance(payload, str):
        return payload[:max_len]

    return ""


def extract_fragments(rows: list[dict]) -> list[SemanticFragment]:
    fragments = []

    for row in rows:
        event_type = row.get("event_type", "")
        if event_type not in _EVENT_TYPE_MAPPING:
            continue

        verb, weight = _EVENT_TYPE_MAPPING[event_type]

        # L3: block login/credential contexts before anything else
        window_title = row.get("window_title", "") or ""
        app_name = row.get("app_name", "") or ""
        content = row.get("content_text") or ""
        sample_for_l3 = _extract_sample_from_payload(content) if content else ""
        if _is_login_context(window_title, app_name, sample_for_l3):
            continue

        app = canonicalize_app(app_name) if app_name else ""

        ts_str = row.get("ts_utc") or row.get("ts_start", "")
        if not ts_str:
            continue
        try:
            if "T" in ts_str:
                if "+" in ts_str or "Z" in ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                else:
                    ts = datetime.fromisoformat(ts_str)
            else:
                continue
        except (ValueError, AttributeError):
            continue

        sample = ""
        if verb in ("type", "paste", "view"):
            payload = row.get("content_text") or row.get("payload", "")
            if payload:
                sample = _extract_sample_from_payload(payload, max_len=80)
                if _contains_privacy_keyword(sample):
                    continue
                # L1 + L2 filters (only for type/paste)
                if not _passes_l1(sample, verb):
                    continue
                if not _passes_l2(sample, verb):
                    continue
                # L5: pinyin stream filter (only for type/paste)
                if verb in ("type", "paste") and _looks_like_unbroken_run(sample):
                    continue

        object_hint = title_to_object_hint(window_title) if window_title else ""

        source_id = row.get("id", 0)
        if not isinstance(source_id, int):
            try:
                source_id = int(source_id)
            except (ValueError, TypeError):
                continue

        fragment = SemanticFragment(
            ts=ts,
            app=app,
            verb=verb,
            object_hint=object_hint,
            sample=sample,
            weight=weight,
            source_id=source_id,
        )
        fragments.append(fragment)

    return fragments


def aggregate_to_slices(
    fragments: list[SemanticFragment],
    window_seconds: int = 300,
) -> list[EvidenceSlice]:
    if not fragments:
        return []

    fragments_sorted = sorted(fragments, key=lambda f: f.ts)

    slices: dict[tuple[str, int], list[SemanticFragment]] = {}

    for frag in fragments_sorted:
        window_idx = int(frag.ts.timestamp()) // window_seconds
        key = (frag.app, window_idx)

        if key not in slices:
            slices[key] = []
        slices[key].append(frag)

    result = []
    for (app, _), frags_in_window in sorted(slices.items(), key=lambda x: min(f.ts for f in x[1])):
        if not frags_in_window:
            continue

        ts_start = min(f.ts for f in frags_in_window)
        ts_end = max(f.ts for f in frags_in_window)

        verbs_set = set()
        object_hints_set = {}
        weight_sum = 0.0
        source_ids = []

        for frag in frags_in_window:
            verbs_set.add(frag.verb)
            if frag.object_hint:
                object_hints_set[frag.object_hint] = True
            weight_sum += frag.weight
            source_ids.append(frag.source_id)

        samples_by_weight = sorted(frags_in_window, key=lambda f: f.weight, reverse=True)
        samples = [f.sample for f in samples_by_weight if f.sample][:3]

        object_hints_list = list(object_hints_set.keys())[:5]

        slice_obj = EvidenceSlice(
            ts_start=ts_start,
            ts_end=ts_end,
            app=app,
            verbs=sorted(list(verbs_set)),
            object_hints=object_hints_list,
            samples=samples,
            fragment_count=len(frags_in_window),
            weight_sum=weight_sum,
            source_ids=source_ids,
        )
        result.append(slice_obj)

    return result


def filter_low_quality_slices(slices: list[EvidenceSlice]) -> list[EvidenceSlice]:
    """L4: drop slices whose type/paste samples are near-identical short edits."""
    result = []
    for s in slices:
        type_paste_samples = [
            sample for sample, verb in zip(s.samples, s.verbs * len(s.samples))
            if sample
        ]
        # Rebuild sample list from fragments not available here; use s.samples directly
        # since aggregate_to_slices already filters by weight and they're all meaningful
        candidates = [sample for sample in s.samples if sample]

        if len(candidates) >= 2:
            total_len = sum(len(c) for c in candidates)
            if total_len < _L4_MAX_TOTAL_LEN:
                # compute average pairwise distance between adjacent samples
                distances = [
                    _levenshtein(candidates[i], candidates[i + 1])
                    for i in range(len(candidates) - 1)
                ]
                avg_dist = sum(distances) / len(distances)
                if avg_dist < _L4_MAX_AVG_DISTANCE:
                    continue  # drop this slice

        result.append(s)
    return result


def extract_clean_slices(rows: list[dict], window_seconds: int = 300) -> list[EvidenceSlice]:
    """High-level helper: extract → aggregate → L4 filter."""
    fragments = extract_fragments(rows)
    slices = aggregate_to_slices(fragments, window_seconds=window_seconds)
    return filter_low_quality_slices(slices)


def render_slices_for_pass1(slices: list[EvidenceSlice]) -> list[str]:
    rendered = []

    for s in slices:
        start_time = s.ts_start.strftime("%H:%M")
        end_time = s.ts_end.strftime("%H:%M")
        time_window = f"{start_time}-{end_time}"

        verbs_str = "/".join(s.verbs) if s.verbs else "action"
        hints_str = " ".join(s.object_hints) if s.object_hints else ""

        samples_str = " / ".join(s.samples) if s.samples else ""

        if hints_str and samples_str:
            line = f"{time_window} 在 {s.app} {{{verbs_str}}} {hints_str}：{samples_str}"
        elif hints_str:
            line = f"{time_window} 在 {s.app} {{{verbs_str}}} {hints_str}"
        elif samples_str:
            line = f"{time_window} 在 {s.app} {{{verbs_str}}}：{samples_str}"
        else:
            line = f"{time_window} 在 {s.app} {{{verbs_str}}}"

        rendered.append(line)

    return rendered


_KEYBOARD_INPUT_TYPES = frozenset({
    "keyboard_chunk_capture", "keyboard_chunk",
    "ax_text_capture", "ax_text",
    "clipboard_copy", "clipboard",
})


def filter_noisy_raw_events(rows: list[dict]) -> list[dict]:
    """Row-level hygiene filter: apply L1-L4 to raw events before obsidian export/narrative.

    L3: block login/credential contexts (window_title or app_name).
    L1+L2: only for keyboard input types; drop short/low-entropy text.
    L4: for each 5min bucket (app,window,ts//300), drop if >=2 items with
         avg_levenshtein < 2 and total_len < 15.
    """
    if not rows:
        return []

    # L3 pass: remove login contexts, record source idx for L1-L4 and others
    l3_kept = []
    for idx, row in enumerate(rows):
        window_title = row.get("window_title", "") or ""
        app_name = row.get("app_name", "") or ""
        content = row.get("content_text") or ""
        sample_for_l3 = _extract_sample_from_payload(content) if content else ""
        if _is_login_context(window_title, app_name, sample_for_l3):
            continue
        l3_kept.append((idx, row))

    if not l3_kept:
        return []

    # Separate keyboard input rows from others
    keyboard_rows = []
    other_rows = []
    for idx, row in l3_kept:
        event_type = row.get("event_type", "")
        if event_type in _KEYBOARD_INPUT_TYPES:
            keyboard_rows.append((idx, row))
        else:
            other_rows.append((idx, row))

    # L1+L2 filter on keyboard rows only
    l1l2_kept = []
    for idx, row in keyboard_rows:
        payload = row.get("content_text") or row.get("payload", "")
        if not payload:
            # No content: keep the row
            l1l2_kept.append((idx, row))
            continue

        sample = _extract_sample_from_payload(payload)

        # Determine verb type for L1/L2 checks
        event_type = row.get("event_type", "")
        verb, _ = _EVENT_TYPE_MAPPING.get(event_type, ("unknown", 0))

        # If sample is empty after extraction (e.g., JSON number), skip L1/L2
        if not sample:
            # Can't extract text from this payload; keep the row
            l1l2_kept.append((idx, row))
            continue

        # L1: short input filter
        if not _passes_l1(sample, verb):
            continue
        # L2: entropy filter
        if not _passes_l2(sample, verb):
            continue
        # L5: pinyin stream filter (only for type/paste)
        if verb in ("type", "paste") and _looks_like_unbroken_run(sample):
            continue

        l1l2_kept.append((idx, row))

    # L4 filter: group keyboard rows by (app_name, window_title, ts_start//300)
    # then drop groups with >=2 items, avg_levenshtein < 2, total_len < 15
    def get_bucket_key(row: dict) -> tuple:
        app_name = row.get("app_name", "") or ""
        window_title = row.get("window_title", "") or ""
        ts_str = row.get("ts_start", "") or row.get("ts_utc", "")
        ts_bucket = 0
        if ts_str:
            try:
                if "T" in ts_str:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00") if "Z" in ts_str else ts_str)
                    ts_bucket = int(ts.timestamp()) // 300
            except (ValueError, AttributeError):
                pass
        return (app_name, window_title, ts_bucket)

    buckets: dict[tuple, list[tuple[int, dict]]] = {}
    for idx, row in l1l2_kept:
        key = get_bucket_key(row)
        if key not in buckets:
            buckets[key] = []
        buckets[key].append((idx, row))

    l4_kept_indices = set()
    for bucket_rows in buckets.values():
        if len(bucket_rows) < 2:
            # Single item or empty bucket: keep all
            for idx, _ in bucket_rows:
                l4_kept_indices.add(idx)
        else:
            # Multiple items: check edit distance
            samples = [
                _extract_sample_from_payload(row.get("content_text") or row.get("payload", ""))
                for _, row in bucket_rows
            ]
            total_len = sum(len(s) for s in samples)

            if total_len >= _L4_MAX_TOTAL_LEN:
                # Total length acceptable: keep all
                for idx, _ in bucket_rows:
                    l4_kept_indices.add(idx)
            else:
                # Total length small; check avg distance
                distances = [
                    _levenshtein(samples[i], samples[i + 1])
                    for i in range(len(samples) - 1)
                ]
                avg_dist = sum(distances) / len(distances) if distances else 0
                if avg_dist >= _L4_MAX_AVG_DISTANCE:
                    # Distance acceptable: keep all
                    for idx, _ in bucket_rows:
                        l4_kept_indices.add(idx)
                # else: all dropped (too similar + too short)

    # Combine: kept keyboard rows (after L4) + all other rows, preserving order
    result_indices = set()
    for idx, _ in l1l2_kept:
        if idx in l4_kept_indices:
            result_indices.add(idx)
    for idx, _ in other_rows:
        result_indices.add(idx)

    return [row for idx, row in enumerate(rows) if idx in result_indices]
