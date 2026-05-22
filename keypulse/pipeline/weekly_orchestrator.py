from __future__ import annotations

import json
import os
import re
import hashlib
import time
import tomllib
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from keypulse.config import Config
from keypulse.i18n import current_lang
from keypulse.integrations import resolve_active_sink
from keypulse.hud.state import read_hud_state
from keypulse.obsidian.principle_exporter import list_week_principles
from keypulse.pipeline.daily_summary import (
    build_topic_status_snapshot_from_narrative,
    merge_topic_status_snapshots,
    read_daily_summary,
)
from keypulse.pipeline.artifact_writer import write_artifact
from keypulse.pipeline.dimension_stats import compute_five_dimensions, render_key_data_section
from keypulse.pipeline.holiday_strategy import build_holiday_context
from keypulse.pipeline.holiday_templates import build_holiday_system_injection
from keypulse.pipeline.llm_errors import classify_llm_error
from keypulse.pipeline.model import LLMCallError, ModelGateway, load_model_gateway
from keypulse.pipeline.onboarding import read_profile
from keypulse.pipeline.run_record import RunRecorder
from keypulse.pipeline.topic_status import TopicStatusSnapshot, compute_topic_status
from keypulse.pipeline.degraded_content import generate_degraded_topic
from keypulse.pipeline.quality_score import QualityBreakdown, append_quality_log, compute_quality_score
from keypulse.pipeline.weekly_topic_anchor import (
    anchor_note_filename,
    load_weekly_anchors,
    save_weekly_anchors,
    write_anchor_note,
)
from keypulse.pipeline.weekly_validator import ValidationFailure, validate_weekly_output
from keypulse.prompts.loader import load_prompt
from keypulse.store.repository import set_state
from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.paths import get_data_dir


_INPUT_MARKER_BEGIN = "<<INPUT_JSON>>"
_INPUT_MARKER_END = "<<END_INPUT_JSON>>"
_TOPIC_RE = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
_ENTRY_DATE_RE = re.compile(r"^-\s*(\d{4}-\d{2}-\d{2})\b")
_ENTRY_EVENTS_RE = re.compile(r"\|\s*(\d+)\s+events?\s*\|", re.IGNORECASE)
_RELATED_EVENT_ID_RE = re.compile(r"\|(.*?)\]\]")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,29}")
_WEEKLY_VALIDATOR_MAX_RETRIES = 2
_WEEKLY_MIN_DAILY_COUNT = 3
_WEEKLY_CACHE_MEMORY_SIZE = 32
_WEEKLY_CIRCUIT_THRESHOLD = 5
_WEEKLY_CIRCUIT_OPEN_SEC = 1800.0
_BRACKET_TAG_RE = re.compile(r"\[(DECISION|SHIPPED|BLOCKED|KEY)\s*:\s*(.+?)\]", re.IGNORECASE)
_STRONG_DECISION_MARKERS = ("决定", "拍板", "敲定", "确定", "定了")
_STRONG_OUTPUT_MARKERS = ("落地", "上线", "发布", "提交", "合并", "完成", "跑通", "修复")
_STRONG_BLOCKED_MARKERS = ("卡点", "阻塞", "失败", "报错", "无法", "问题")
_WIKILINK_RE = re.compile(r"\[\[[^\]]+\]\]")
_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_STATUS_PRIORITY = {
    "new": 0,
    "accelerating": 1,
    "revived": 2,
    "ongoing": 3,
    "steady": 4,
    "declining": 5,
}


class WeeklyOrchestratorError(RuntimeError):
    """Raised when weekly orchestration cannot complete."""


@dataclass(frozen=True)
class CostSnapshot:
    total_cost_usd: float
    calls: int
    cache_hits: int


@dataclass(frozen=True)
class WeeklyLLMResult:
    content: Any
    source: str
    attempts: int
    reason: str = ""
    error_kind: str = ""


@dataclass
class WeeklyRunStats:
    l4_source: str = "degraded"
    l5_sources: list[str] | None = None
    l6_source: str = "degraded"
    llm_calls: int = 0
    cache_hits: int = 0
    degraded_calls: int = 0
    cost_usd: float = 0.0

    def l5_source_label(self) -> str:
        values = sorted(set(self.l5_sources or ["degraded"]))
        return "/".join(values)


class WeeklyCircuitBreaker:
    def __init__(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def is_open(self) -> bool:
        if time.monotonic() >= self.open_until:
            self.open_until = 0.0
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= _WEEKLY_CIRCUIT_THRESHOLD:
            self.open_until = time.monotonic() + _WEEKLY_CIRCUIT_OPEN_SEC


_WEEKLY_CIRCUIT = WeeklyCircuitBreaker()
_WEEKLY_MEMORY_CACHE: OrderedDict[str, Any] = OrderedDict()


def _week_start(week_str: str) -> date_cls:
    text = str(week_str or "").strip()
    if "-W" not in text:
        raise ValueError(f"invalid ISO week: {week_str}")
    year_text, week_text = text.split("-W", 1)
    try:
        return date_cls.fromisocalendar(int(year_text), int(week_text), 1)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week: {week_str}") from exc


def _week_dates(week_str: str) -> list[str]:
    start = _week_start(week_str)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(7)]


def _stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _sha1_json(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def _parse_llm_json_robust(text: str) -> Any | None:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None

    def _try_parse(candidate: str) -> Any | None:
        try:
            return json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            return None

    def _strip_trailing_commas(candidate: str) -> str:
        return re.sub(r",(\s*[}\]])", r"\1", candidate)

    def _single_to_double_quotes(candidate: str) -> str:
        def _quote(value: str) -> str:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        # key: 'foo': -> "foo":
        repaired = re.sub(
            r"([{\[,]\s*)'([^'\\]*(?:\\.[^'\\]*)*)'(\s*:)",
            lambda m: f"{m.group(1)}{_quote(m.group(2))}{m.group(3)}",
            candidate,
        )
        # value: : 'bar' -> : "bar"
        repaired = re.sub(
            r"(:\s*)'([^'\\]*(?:\\.[^'\\]*)*)'(\s*[,}\]])",
            lambda m: f"{m.group(1)}{_quote(m.group(2))}{m.group(3)}",
            repaired,
        )
        # array item: [ 'x', 'y' ] -> [ "x", "y" ]
        repaired = re.sub(
            r"([,\[]\s*)'([^'\\]*(?:\\.[^'\\]*)*)'(\s*[,}\]])",
            lambda m: f"{m.group(1)}{_quote(m.group(2))}{m.group(3)}",
            repaired,
        )
        return repaired

    candidates: list[str] = [stripped]
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, re.IGNORECASE):
        chunk = str(match.group(1) or "").strip()
        if chunk:
            candidates.append(chunk)
    for left, right in (("{", "}"), ("[", "]")):
        start = stripped.find(left)
        end = stripped.rfind(right)
        if start >= 0 and end > start:
            candidates.append(stripped[start : end + 1].strip())

    for candidate in candidates:
        parsed = _try_parse(candidate)
        if parsed is not None:
            return parsed
        repaired = _strip_trailing_commas(candidate)
        parsed = _try_parse(repaired)
        if parsed is not None:
            return parsed
        repaired_quotes = _single_to_double_quotes(repaired)
        parsed = _try_parse(repaired_quotes)
        if parsed is not None:
            return parsed
    return None


def _stable_l6_cache_key(
    week_str: str,
    l5_topics: list[dict[str, Any]],
    daily_corpus: str,
    profile: dict[str, Any],
) -> str:
    payload = {
        "week": week_str,
        "topics": sorted(str(item.get("slug") or "") for item in l5_topics if isinstance(item, dict)),
        "corpus_hash": hashlib.sha1(str(daily_corpus).encode("utf-8")).hexdigest()[:16],
        "profile": {
            "work_type": profile.get("work_type"),
            "region": profile.get("region"),
        },
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:16]


def _weekly_cache_dir(week_str: str) -> Path:
    target = get_data_dir() / "weekly-cache" / week_str
    target.mkdir(parents=True, exist_ok=True)
    return target


def _weekly_cache_get(week_str: str, level: str, cache_key: str) -> Any | None:
    memory_key = f"{week_str}:{level}:{cache_key}"
    if memory_key in _WEEKLY_MEMORY_CACHE:
        value = _WEEKLY_MEMORY_CACHE.pop(memory_key)
        _WEEKLY_MEMORY_CACHE[memory_key] = value
        return value

    path = _weekly_cache_dir(week_str) / f"{level}_{cache_key}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    value = payload.get("content") if isinstance(payload, dict) else None
    _weekly_cache_put(week_str, level, cache_key, value)
    return value


def _weekly_cache_put(week_str: str, level: str, cache_key: str, content: Any) -> None:
    memory_key = f"{week_str}:{level}:{cache_key}"
    _WEEKLY_MEMORY_CACHE[memory_key] = content
    while len(_WEEKLY_MEMORY_CACHE) > _WEEKLY_CACHE_MEMORY_SIZE:
        _WEEKLY_MEMORY_CACHE.popitem(last=False)

    path = _weekly_cache_dir(week_str) / f"{level}_{cache_key}.json"
    payload = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "level": level,
        "key": cache_key,
        "content": content,
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _append_weekly_cost_row(
    *,
    capability: str,
    source: str,
    attempts: int,
    reason: str = "",
    cost_usd: float = 0.0,
) -> None:
    path = get_data_dir() / "cost.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "capability": capability,
        "model": "weekly-wrapper",
        "tier": "standard",
        "in_tokens": 0,
        "out_tokens": 0,
        "cost_usd": float(cost_usd),
        "cache_hit": source == "cache",
        "prompt_version": capability,
        "source": source,
        "attempts": int(attempts),
        "reason": reason,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _sleep_backoff(seconds: float) -> None:
    if os.environ.get("KEYPULSE_WEEKLY_RETRY_SLEEP") == "0":
        return
    time.sleep(seconds)


def _is_retryable_weekly_error(exc: Exception) -> bool:
    lowered = str(exc or "").lower()
    if re.search(r"\b(4\d\d|5\d\d)\b", lowered):
        return True
    if "http" in lowered:
        return True
    if "schema" in lowered or "validation" in lowered:
        return True
    if "empty" in lowered or "none" in lowered:
        return True
    if "timeout" in lowered or "timed out" in lowered:
        return True
    return isinstance(exc, (LLMCallError, OSError, RuntimeError, ValueError, KeyError, TypeError))


def _call_weekly_llm(
    *,
    gateway: ModelGateway,
    week_str: str,
    level: str,
    capability: str,
    prompt: str,
    input_data: dict[str, Any],
    cache_key: str,
    attempts: int,
    stats: WeeklyRunStats,
    validator=None,
) -> WeeklyLLMResult:
    cached = _weekly_cache_get(week_str, level, cache_key)
    if cached is not None and (not callable(validator) or bool(validator(cached))):
        stats.cache_hits += 1
        _append_weekly_cost_row(capability=capability, source="cache", attempts=0, reason="")
        return WeeklyLLMResult(content=cached, source="cache", attempts=0)

    if _WEEKLY_CIRCUIT.is_open():
        stats.degraded_calls += 1
        _append_weekly_cost_row(capability=capability, source="degraded", attempts=0, reason="circuit_open")
        return WeeklyLLMResult(content=None, source="degraded", attempts=0, reason="circuit_open", error_kind="unknown")

    backoffs = [1.0, 2.0]
    last_error = ""
    last_exc: Exception | None = None
    max_attempts = max(1, min(int(attempts or 1), 3))
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            _sleep_backoff(backoffs[min(attempt - 2, len(backoffs) - 1)])
        try:
            output = gateway.call(capability, prompt, input_data=input_data)
            if output in (None, "", [], {}):
                raise ValueError("empty JSON output")
            if isinstance(output, str) and capability in {"L4_weekly_reconcile", "L5_weekly_main_narrative", "L6_explorer", "L7_anchor_derivation"}:
                repaired = _parse_llm_json_robust(output)
                output = repaired if repaired is not None else output
            if capability == "L6_explorer":
                output = _normalize_l6_output(output)
            if callable(validator) and not bool(validator(output)):
                raise ValueError("schema validation failed")
            _WEEKLY_CIRCUIT.record_success()
            _weekly_cache_put(week_str, level, cache_key, output)
            stats.llm_calls += 1
            _append_weekly_cost_row(capability=capability, source="llm", attempts=attempt, reason="")
            return WeeklyLLMResult(content=output, source="llm", attempts=attempt)
        except (LLMCallError, OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            last_exc = exc
            last_error = f"{type(exc).__name__}:{exc}"
            if attempt >= max_attempts or not _is_retryable_weekly_error(exc):
                break

    _WEEKLY_CIRCUIT.record_failure()
    stats.degraded_calls += 1
    _append_weekly_cost_row(capability=capability, source="degraded", attempts=max_attempts, reason=last_error or "llm_failed")
    error_kind = classify_llm_error(last_exc).value if last_exc is not None else "unknown"
    return WeeklyLLMResult(
        content=None,
        source="degraded",
        attempts=max_attempts,
        reason=last_error or "llm_failed",
        error_kind=error_kind,
    )


def _build_prompt(spec_body: str, capability: str, payload: dict[str, Any]) -> str:
    rendered_spec = spec_body.replace("{{lang}}", current_lang()).strip()
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    return "\n".join([f"CAPABILITY: {capability}", rendered_spec, _INPUT_MARKER_BEGIN, rendered, _INPUT_MARKER_END])


def _format_weekly_validation_feedback(failures: list[ValidationFailure]) -> str:
    if not failures:
        return ""
    lines = ["周报输出未通过 validator，请严格按规则重写 JSON（不要解释过程，不要改 schema）：", ""]
    for item in failures:
        lines.append(f"- field={item.field} rule={item.rule}: {item.detail}")
    return "\n".join(lines).strip()


def _augment_l5_output_with_status(l5_output: dict[str, Any], topics_to_write: list[dict[str, Any]]) -> dict[str, Any]:
    by_slug_status: dict[str, str] = {}
    by_slug_anchor_dates: dict[str, list[str]] = {}
    by_slug_previous_week_narrative: dict[str, str] = {}
    for row in topics_to_write:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "").strip()
        status = str(row.get("status") or "").strip()
        if slug and status:
            by_slug_status[slug] = status
        if slug:
            weekly_entries = row.get("weekly_entries") or []
            dates = [
                str(item.get("date") or "").strip()
                for item in weekly_entries
                if isinstance(item, dict) and str(item.get("date") or "").strip()
            ]
            by_slug_anchor_dates[slug] = dates
            previous_week_narrative = str(row.get("previous_week_narrative") or "").strip()
            if previous_week_narrative:
                by_slug_previous_week_narrative[slug] = previous_week_narrative

    narratives = l5_output.get("narratives") if isinstance(l5_output, dict) else None
    if not isinstance(narratives, list):
        return dict(l5_output) if isinstance(l5_output, dict) else {}

    augmented: list[dict[str, Any]] = []
    for item in narratives:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        status = by_slug_status.get(slug, "")
        anchor_dates = by_slug_anchor_dates.get(slug, [])
        previous_week_narrative = by_slug_previous_week_narrative.get(slug, "")
        merged = dict(item)
        if status:
            merged["status"] = status
        if anchor_dates:
            merged["allowed_anchor_dates"] = anchor_dates
        if previous_week_narrative:
            merged["previous_week_narrative"] = previous_week_narrative
        augmented.append(merged)

    result = dict(l5_output)
    result["narratives"] = augmented
    return result


def _sanitize_weekly_outputs(
    l5_output: dict[str, Any],
    l6_output: dict[str, Any],
    failures: list[ValidationFailure],
) -> tuple[dict[str, Any], dict[str, Any]]:
    safe_l5 = dict(l5_output) if isinstance(l5_output, dict) else {}
    safe_l6 = dict(l6_output) if isinstance(l6_output, dict) else {}
    if failures:
        safe_l5["quality_status"] = "validator_failed"
        safe_l6["quality_status"] = "validator_failed"

    return safe_l5, safe_l6


def _extract_prompt_payload(prompt: str) -> tuple[str, dict[str, Any]]:
    first_line = prompt.splitlines()[0] if prompt.splitlines() else ""
    capability = first_line.replace("CAPABILITY:", "").strip() if first_line.startswith("CAPABILITY:") else ""

    begin = prompt.find(_INPUT_MARKER_BEGIN)
    end = prompt.find(_INPUT_MARKER_END)
    if begin < 0 or end < 0 or end <= begin:
        return capability, {}
    content = prompt[begin + len(_INPUT_MARKER_BEGIN) : end].strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        payload = {}
    return capability, payload if isinstance(payload, dict) else {}


def _parse_cost_ts(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _weekly_cost_window(week_str: str) -> CostSnapshot:
    start_date = _week_start(week_str)
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    path = get_data_dir() / "cost.jsonl"
    if not path.exists():
        return CostSnapshot(total_cost_usd=0.0, calls=0, cache_hits=0)

    total = 0.0
    calls = 0
    cache_hits = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            ts = _parse_cost_ts(str(row.get("ts") or ""))
            if ts is None or not (start <= ts < end):
                continue
            if str(row.get("source") or "llm") == "llm":
                calls += 1
            total += float(row.get("cost_usd") or 0.0)
            if bool(row.get("cache_hit")) or str(row.get("source") or "") == "cache":
                cache_hits += 1
    return CostSnapshot(total_cost_usd=round(total, 8), calls=calls, cache_hits=cache_hits)


def _load_topic_index() -> list[dict[str, Any]]:
    topics_dir = get_data_dir() / "topics"
    if not topics_dir.exists():
        return []

    result: list[dict[str, Any]] = []
    for topic_path in sorted(topics_dir.glob("*.md")):
        slug = topic_path.stem
        if not _TOPIC_RE.fullmatch(slug):
            continue
        text = topic_path.read_text(encoding="utf-8")

        display_name = slug
        keywords: list[str] = []
        first_seen = ""
        last_seen = ""
        pinned = False
        entry_dates: list[str] = []
        entry_rows: list[dict[str, Any]] = []
        related_event_ids: list[str] = []

        lines = text.splitlines()
        in_frontmatter = False
        in_keywords = False
        in_entries = False
        in_related = False

        if lines and lines[0].strip() == "---":
            in_frontmatter = True

        for idx, line in enumerate(lines):
            stripped = line.strip()

            if in_frontmatter:
                if idx == 0:
                    continue
                if stripped == "---":
                    in_frontmatter = False
                    in_keywords = False
                    continue
                if stripped.startswith("display_name:"):
                    display_name = stripped.split(":", 1)[1].strip() or display_name
                    continue
                if stripped.startswith("first_seen:"):
                    first_seen = stripped.split(":", 1)[1].strip()
                    continue
                if stripped.startswith("last_seen:"):
                    last_seen = stripped.split(":", 1)[1].strip()
                    continue
                if stripped.startswith("pinned:"):
                    pinned = stripped.split(":", 1)[1].strip().lower() == "true"
                    continue
                if stripped.startswith("keywords:"):
                    inline = stripped.split(":", 1)[1].strip()
                    if inline.startswith("[") and inline.endswith("]"):
                        for item in inline.strip("[]").split(","):
                            token = item.strip().strip('"').strip("'").lower()
                            if token:
                                keywords.append(token)
                        in_keywords = False
                    else:
                        in_keywords = True
                    continue
                if in_keywords and stripped.startswith("-"):
                    token = stripped.lstrip("-").strip().strip('"').strip("'").lower()
                    if token:
                        keywords.append(token)
                    continue
                in_keywords = False
                continue

            if stripped == "## Entries":
                in_entries = True
                in_related = False
                continue
            if stripped == "## Related Events":
                in_related = True
                in_entries = False
                continue
            if stripped.startswith("## "):
                in_entries = False
                in_related = False
                continue

            if in_entries:
                matched = _ENTRY_DATE_RE.match(stripped)
                if matched:
                    entry_date = matched.group(1)
                    entry_dates.append(entry_date)
                    events_match = _ENTRY_EVENTS_RE.search(stripped)
                    event_count = int(events_match.group(1)) if events_match else 1
                    entry_rows.append({"date": entry_date, "event_count": max(event_count, 1)})
                continue

            if in_related and stripped.startswith("-"):
                match_id = _RELATED_EVENT_ID_RE.search(stripped)
                if match_id:
                    value = match_id.group(1).strip()
                    if value:
                        related_event_ids.append(value)

        dedup_keywords: list[str] = []
        seen_kw: set[str] = set()
        for item in keywords:
            token = str(item).strip().lower()
            if not token or token in seen_kw:
                continue
            seen_kw.add(token)
            dedup_keywords.append(token)

        result.append(
            {
                "slug": slug,
                "display_name": display_name,
                "keywords": dedup_keywords[:10],
                "first_seen": first_seen or None,
                "last_seen": last_seen or None,
                "entry_dates": sorted(entry_dates),
                "entry_rows": sorted(entry_rows, key=lambda item: str(item.get("date") or "")),
                "related_event_ids": sorted(set(related_event_ids)),
                "pinned": pinned,
            }
        )
    return result


def _build_weekly_history(topics_index: list[dict[str, Any]], week_str: str) -> dict[str, list[int]]:
    start = _week_start(week_str)
    history: dict[str, list[int]] = {}
    for topic in topics_index:
        slug = str(topic.get("slug") or "").strip()
        if not slug:
            continue
        counts_by_week: dict[tuple[int, int], int] = {}
        entry_rows = topic.get("entry_rows")
        if isinstance(entry_rows, list):
            iter_rows = entry_rows
        else:
            iter_rows = [{"date": value, "event_count": 1} for value in (topic.get("entry_dates") or [])]
        for row in iter_rows:
            if not isinstance(row, dict):
                continue
            date_text = str(row.get("date") or "")
            try:
                day = date_cls.fromisoformat(str(date_text))
            except ValueError:
                continue
            if day >= start:
                continue
            iso = day.isocalendar()
            key = (iso.year, iso.week)
            raw_count = row.get("event_count")
            if isinstance(raw_count, bool):
                continue
            if isinstance(raw_count, int):
                event_count = raw_count
            else:
                try:
                    event_count = int(str(raw_count))
                except (TypeError, ValueError):
                    event_count = 1
            if event_count <= 0:
                continue
            counts_by_week[key] = counts_by_week.get(key, 0) + event_count

        ordered = [counts_by_week[key] for key in sorted(counts_by_week.keys())]
        history[slug] = ordered
    return history


def _historical_daily_avg_from_history(history: dict[str, list[int]], lookback_weeks: int = 4) -> float:
    if not history:
        return 0.0
    weekly_totals: list[int] = []
    for week_offset in range(1, lookback_weeks + 1):
        week_total = 0
        has_any = False
        for series in history.values():
            if len(series) < week_offset:
                continue
            has_any = True
            week_total += int(series[-week_offset] or 0)
        if has_any:
            weekly_totals.append(week_total)
    if not weekly_totals:
        return 0.0
    return float(sum(weekly_totals)) / float(len(weekly_totals) * 7)


def _daily_event_counts(daily_summaries: list[dict[str, Any]]) -> list[int]:
    values: list[int] = []
    for daily in daily_summaries:
        raw_total = daily.get("event_count_total")
        if isinstance(raw_total, int) and not isinstance(raw_total, bool):
            values.append(raw_total)
            continue
        cluster_total = sum(
            int(cluster.get("event_count") or 0)
            for cluster in (daily.get("clusters") or [])
            if isinstance(cluster, dict)
        )
        values.append(cluster_total)
    return values


def _load_week_daily_summaries(week_str: str) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for day in _week_dates(week_str):
        payload = read_daily_summary(day)
        if payload is not None:
            summaries.append(_enrich_daily_summary_from_markdown(payload))
    return summaries


def _daily_markdown_path(date_str: str) -> Path:
    sink = resolve_active_sink(Config.load(), persist=False)
    return sink.output_dir / "Daily" / f"{date_str}.md"


def _read_daily_markdown(date_str: str) -> str:
    path = _daily_markdown_path(date_str)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _enrich_daily_summary_from_markdown(payload: dict[str, Any]) -> dict[str, Any]:
    date_text = str(payload.get("date") or "").strip()
    if not date_text:
        return dict(payload)
    snapshot = payload.get("topic_status_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return dict(payload)
    markdown = _read_daily_markdown(date_text)
    inferred = build_topic_status_snapshot_from_narrative(date_text, markdown) if markdown else {}
    enriched = dict(payload)
    enriched["topic_status_snapshot"] = inferred
    return enriched


def _week_topic_snapshot(week_str: str) -> dict[str, dict[str, Any]]:
    snapshots: list[dict[str, dict[str, Any]]] = []
    for daily in _load_week_daily_summaries(week_str):
        snapshot = daily.get("topic_status_snapshot")
        if isinstance(snapshot, dict):
            snapshots.append(snapshot)
    return merge_topic_status_snapshots(snapshots)


def _cross_week_state_diff(week_str: str, current_snapshot: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    previous_start = _week_start(week_str) - timedelta(days=7)
    previous_iso = previous_start.isocalendar()
    previous_week = f"{previous_iso.year}-W{previous_iso.week:02d}"
    previous_snapshot = _week_topic_snapshot(previous_week)
    transitions: list[dict[str, str]] = []
    for slug, payload in sorted(current_snapshot.items()):
        if not isinstance(payload, dict):
            continue
        current_state = str(payload.get("state") or "").strip()
        previous_payload = previous_snapshot.get(slug) if isinstance(previous_snapshot, dict) else None
        previous_state = str((previous_payload or {}).get("state") or "").strip()
        if not previous_state or not current_state or previous_state == current_state:
            continue
        topic_name = str(payload.get("name") or slug).strip() or slug
        transitions.append({"topic": topic_name, "from_state": previous_state, "to_state": current_state})
    return transitions


def _normalize_signal_text(text: str) -> str:
    return " ".join(str(text or "").strip().split())[:220]


def _extract_weekly_signals(week_str: str) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    decisions: list[dict[str, str]] = []
    outputs: list[dict[str, str]] = []
    blockers: list[dict[str, str]] = []
    have_tag = False

    for day in _week_dates(week_str):
        markdown = _read_daily_markdown(day)
        if not markdown:
            continue

        for match in _BRACKET_TAG_RE.finditer(markdown):
            tag = str(match.group(1) or "").upper()
            text = _normalize_signal_text(match.group(2) or "")
            if not text:
                continue
            have_tag = True
            if tag in {"DECISION", "KEY"}:
                decisions.append({"date": day, "text": text, "source": "tag"})
            elif tag == "SHIPPED":
                outputs.append({"date": day, "text": text, "source": "tag"})
            elif tag == "BLOCKED":
                blockers.append({"date": day, "text": text, "source": "tag"})

    if have_tag:
        return decisions, outputs, blockers

    for day in _week_dates(week_str):
        markdown = _read_daily_markdown(day)
        if not markdown:
            continue
        for line in markdown.splitlines():
            text = _normalize_signal_text(line)
            if len(text) < 8 or text.startswith("#"):
                continue
            if any(token in text for token in _STRONG_DECISION_MARKERS):
                decisions.append({"date": day, "text": text, "source": "heuristic"})
            if any(token in text for token in _STRONG_OUTPUT_MARKERS):
                outputs.append({"date": day, "text": text, "source": "heuristic"})
            if any(token in text for token in _STRONG_BLOCKED_MARKERS):
                blockers.append({"date": day, "text": text, "source": "heuristic"})

    return decisions[:12], outputs[:12], blockers[:12]


def _topic_tokens(topic: dict[str, Any]) -> set[str]:
    values = [str(topic.get("slug") or ""), str(topic.get("name") or ""), str(topic.get("display_name") or "")]
    tokens = {value.strip().lower() for value in values if value.strip()}
    for value in values:
        for token in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", value):
            tokens.add(token.lower())
    return {token for token in tokens if token}


def _signal_matches_topic(signal_text: str, topic: dict[str, Any]) -> bool:
    text = str(signal_text or "").lower()
    tokens = _topic_tokens(topic)
    return any(token in text for token in tokens)


def _signals_for_topic(signals: list[dict[str, str]], topic: dict[str, Any], limit: int = 3) -> list[dict[str, str]]:
    matched = [item for item in signals if _signal_matches_topic(str(item.get("text") or ""), topic)]
    if matched:
        return matched[:limit]
    return signals[:limit]

def _snapshot_week_entries(daily_summaries: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    cluster_counts: dict[tuple[str, str], int] = {}
    cluster_lines: dict[tuple[str, str], str] = {}
    for daily in daily_summaries:
        date_text = str(daily.get("date") or "").strip()
        for cluster in daily.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            slug = str(cluster.get("slug") or "").strip()
            if not slug:
                continue
            cluster_counts[(date_text, slug)] = int(cluster.get("event_count") or 1)
            cluster_lines[(date_text, slug)] = str(cluster.get("narrative_one_line") or "").strip()
            result.setdefault(slug, []).append(
                {
                    "date": date_text,
                    "narrative_one_line": str(cluster.get("narrative_one_line") or cluster.get("display_name") or slug).strip(),
                    "event_count": int(cluster.get("event_count") or 1),
                }
            )

        snapshot = daily.get("topic_status_snapshot") or {}
        if not isinstance(snapshot, dict):
            continue
        for slug, item in snapshot.items():
            if not isinstance(item, dict):
                continue
            key = str(slug or "").strip()
            if not key:
                continue
            dates = [str(v) for v in (item.get("evidence_dates") or [date_text]) if str(v).strip()]
            for evidence_date in dates or [date_text]:
                result.setdefault(key, []).append(
                    {
                        "date": evidence_date,
                        "narrative_one_line": cluster_lines.get((evidence_date, key), str(item.get("name") or key)),
                        "event_count": cluster_counts.get((evidence_date, key), 1),
                    }
                )
    for slug, entries in list(result.items()):
        dedup: dict[str, dict[str, Any]] = {}
        for entry in entries:
            date_text = str(entry.get("date") or "").strip()
            if not date_text:
                continue
            if date_text not in dedup:
                dedup[date_text] = dict(entry)
            else:
                dedup[date_text]["event_count"] = int(dedup[date_text].get("event_count") or 0) + int(entry.get("event_count") or 0)
        result[slug] = [dedup[key] for key in sorted(dedup.keys())]
    return result


def _collect_topic_week_entries(daily_summaries: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    mapping: dict[str, list[dict[str, str]]] = {}
    for daily in daily_summaries:
        date = str(daily.get("date") or "")
        for cluster in daily.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            slug = str(cluster.get("slug") or "").strip()
            if not slug or slug == "misc":
                continue
            mapping.setdefault(slug, []).append(
                {
                    "date": date,
                    "narrative_one_line": str(cluster.get("narrative_one_line") or "").strip(),
                }
            )
    return mapping


def _topic_state_from_snapshots(slug: str, daily_summaries: list[dict[str, Any]]) -> tuple[str, str]:
    states: list[str] = []
    name = slug
    for daily in daily_summaries:
        for cluster in daily.get("clusters") or []:
            if isinstance(cluster, dict) and str(cluster.get("slug") or "").strip() == slug:
                name = str(cluster.get("display_name") or name).strip() or name
                states.append("in_progress")
        snapshot = daily.get("topic_status_snapshot") or {}
        if not isinstance(snapshot, dict):
            continue
        payload = snapshot.get(slug)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("name") or "").strip():
            name = str(payload.get("name")).strip()
        state = str(payload.get("state") or "").strip()
        if state:
            states.append(state)
    return name, _aggregate_daily_states(states)


def _aggregate_daily_states(states: list[str]) -> str:
    valid = [item for item in states if item in {"started", "in_progress", "completed", "blocked"}]
    if not valid:
        return "in_progress"
    if valid[-1] == "completed":
        return "completed"
    if "blocked" in valid:
        return "blocked"
    if "in_progress" in valid:
        return "in_progress"
    return "started"


def _fallback_l4_topics(daily_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries_by_slug = _snapshot_week_entries(daily_summaries)
    topics: list[dict[str, Any]] = []
    for slug in sorted(entries_by_slug.keys()):
        name, state = _topic_state_from_snapshots(slug, daily_summaries)
        topics.append(
            {
                "slug": slug,
                "name": name,
                "state": state,
                "weekly_entries": entries_by_slug.get(slug, []),
            }
        )
    topics.sort(key=lambda item: (-sum(int(entry.get("event_count") or 1) for entry in item.get("weekly_entries") or []), str(item.get("slug") or "")))
    return topics


def _normalize_l4_output(output: Any, daily_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(output, str):
        parsed = _parse_llm_json_robust(output)
        output = parsed if parsed is not None else output
    raw = output.get("topics") if isinstance(output, dict) else output
    fallback_by_slug = {str(item.get("slug") or ""): item for item in _fallback_l4_topics(daily_summaries)}
    result: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip()
            if not slug:
                continue
            fallback = fallback_by_slug.get(slug, {})
            entries = item.get("weekly_entries") if isinstance(item.get("weekly_entries"), list) else fallback.get("weekly_entries", [])
            state = str(item.get("state") or fallback.get("state") or "in_progress").strip()
            if state not in {"started", "in_progress", "completed", "blocked"}:
                state = "in_progress"
            result.append(
                {
                    "slug": slug,
                    "name": str(item.get("name") or item.get("display_name") or fallback.get("name") or slug),
                    "state": state,
                    "weekly_entries": [entry for entry in entries if isinstance(entry, dict)],
                }
            )
    return result or list(fallback_by_slug.values())


def _weekly_entry_count(topic: dict[str, Any]) -> int:
    return sum(int(entry.get("event_count") or 1) for entry in (topic.get("weekly_entries") or []) if isinstance(entry, dict))


def _state_status_for_validator(state: str) -> str:
    return {
        "started": "new",
        "in_progress": "ongoing",
        "completed": "steady",
        "blocked": "declining",
    }.get(state, "steady")


def _collect_merge_candidate_pairs(daily_summaries: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for daily in daily_summaries:
        for cluster in daily.get("clusters") or []:
            if not isinstance(cluster, dict):
                continue
            slug = str(cluster.get("slug") or "").strip()
            if not slug:
                continue
            for item in cluster.get("merge_candidate_with") or []:
                other = str(item or "").strip()
                if not other or other == slug:
                    continue
                pair = tuple(sorted((slug, other)))
                pairs.add(pair)
    return sorted(pairs)


def _keywords(tokens: list[str], text: str) -> set[str]:
    result = set(tokens)
    result.update(_WORD_RE.findall(text.lower()))
    return {item for item in result if item}


def _topic_map(topics_index: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("slug")): dict(item) for item in topics_index if str(item.get("slug") or "").strip()}


def _affinity_score(topic_a: dict[str, Any], topic_b: dict[str, Any], week_entries: dict[str, list[dict[str, str]]]) -> float:
    kw_a = set(str(item).lower() for item in (topic_a.get("keywords") or []))
    kw_b = set(str(item).lower() for item in (topic_b.get("keywords") or []))
    keyword_overlap = len(kw_a & kw_b) * 3.0

    ent_a = _keywords(list(kw_a), " ".join([str(topic_a.get("display_name") or ""), str(topic_a.get("slug") or "")]))
    ent_b = _keywords(list(kw_b), " ".join([str(topic_b.get("display_name") or ""), str(topic_b.get("slug") or "")]))
    shared_entities = len(ent_a & ent_b) * 2.5

    ev_a = set(str(item) for item in (topic_a.get("related_event_ids") or []))
    ev_b = set(str(item) for item in (topic_b.get("related_event_ids") or []))
    shared_events = len(ev_a & ev_b) * 1.5

    days_a = {str(item.get("date") or "") for item in week_entries.get(str(topic_a.get("slug")), [])}
    days_b = {str(item.get("date") or "") for item in week_entries.get(str(topic_b.get("slug")), [])}
    shared_days = len(days_a & days_b) * 1.0

    return round(keyword_overlap + shared_entities + shared_events + shared_days, 3)


def _topic_summary_for_l4(topic: dict[str, Any], week_entries: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    slug = str(topic.get("slug") or "")
    return {
        "display_name": str(topic.get("display_name") or slug),
        "keywords": [str(item) for item in (topic.get("keywords") or [])],
        "weekly_entries": list(week_entries.get(slug, [])),
    }


def _parse_markdown_lines(text: str, heading: str) -> list[str]:
    lines = text.splitlines()
    in_section = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == heading:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("-"):
            out.append(stripped)
    return out


def _append_section_lines(text: str, heading: str, lines_to_add: list[str]) -> str:
    lines = text.splitlines()
    start = None
    end = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        lines.extend(["", heading])
        start = len(lines)

    end = len(lines)
    for idx in range(start, len(lines)):
        if lines[idx].strip().startswith("## "):
            end = idx
            break

    current = [lines[idx].strip() for idx in range(start, end) if lines[idx].strip().startswith("-")]
    merged = list(current)
    for line in lines_to_add:
        if line not in merged:
            merged.append(line)

    new_lines = lines[:start] + [item for item in merged] + lines[end:]
    return "\n".join(new_lines).rstrip() + "\n"


def _extract_topic_evidence_from_markdown(markdown: str, topic_name: str) -> list[dict[str, str]]:
    target = str(topic_name or "").strip()
    if not target:
        return []
    lines = str(markdown or "").splitlines()
    evidence: list[dict[str, str]] = []
    in_section = False
    buffer: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            if in_section and buffer:
                evidence.append({"heading": target, "text": "\n".join(buffer).strip()})
            heading = stripped[4:].strip()
            in_section = heading == target
            buffer = []
            continue
        if in_section and stripped.startswith("## "):
            if buffer:
                evidence.append({"heading": target, "text": "\n".join(buffer).strip()})
            in_section = False
            buffer = []
            continue
        if in_section and stripped:
            buffer.append(stripped)
    if in_section and buffer:
        evidence.append({"heading": target, "text": "\n".join(buffer).strip()})
    return evidence


def _topic_evidence(topic: dict[str, Any], daily_summaries: list[dict[str, Any]]) -> list[dict[str, str]]:
    name = str(topic.get("name") or topic.get("display_name") or topic.get("slug") or "").strip()
    dates = {str(entry.get("date") or "") for entry in (topic.get("weekly_entries") or []) if isinstance(entry, dict)}
    result: list[dict[str, str]] = []
    for date_text in sorted(dates):
        markdown = _read_daily_markdown(date_text)
        snippets = _extract_topic_evidence_from_markdown(markdown, name)
        if not snippets:
            for entry in topic.get("weekly_entries") or []:
                if isinstance(entry, dict) and str(entry.get("date") or "") == date_text:
                    snippets = [{"heading": name, "text": str(entry.get("narrative_one_line") or name)}]
                    break
        for snippet in snippets:
            text = str(snippet.get("text") or "").strip()
            if text:
                result.append({"date": date_text, "text": text[:800]})
    return result


def _collect_daily_topic_signals(
    topic: dict[str, Any], daily_summaries: list[dict[str, Any]]
) -> dict[str, list[dict[str, str]]]:
    """从 daily v3 topics[] 按 cluster.slug / display_name 反查决策/产出/状态。

    daily topics[].events_ref 含 cluster.slug；display 与 cluster.display_name 一致。
    返回三个数组（每条带 date），由调用方塞进 L5 input 作为权威事实。
    """
    cluster_slug = str(topic.get("slug") or "").strip()
    display = str(topic.get("name") or topic.get("display_name") or "").strip()
    decisions: list[dict[str, str]] = []
    shipped: list[dict[str, str]] = []
    anchor_states: list[dict[str, str]] = []
    for daily in daily_summaries:
        date_text = str(daily.get("date") or "").strip()
        for entry in daily.get("topics") or []:
            if not isinstance(entry, dict):
                continue
            refs = {str(x or "").strip() for x in (entry.get("events_ref") or [])}
            entry_display = str(entry.get("display") or "").strip()
            if cluster_slug and cluster_slug in refs:
                matched = True
            elif display and entry_display and display == entry_display:
                matched = True
            else:
                matched = False
            if not matched:
                continue
            for text in entry.get("decisions") or []:
                text_str = str(text or "").strip()
                if text_str:
                    decisions.append({"date": date_text, "text": text_str[:300]})
            for text in entry.get("shipped") or []:
                text_str = str(text or "").strip()
                if text_str:
                    shipped.append({"date": date_text, "text": text_str[:300]})
            state = str(entry.get("anchor_state") or "").strip()
            if state:
                anchor_states.append({"date": date_text, "state": state})
    return {
        "daily_decisions": decisions[:6],
        "daily_shipped": shipped[:6],
        "daily_anchor_states": anchor_states[:7],
    }


def _load_hud_inputs_for_week(week_str: str) -> list[dict[str, str]]:
    dates = set(_week_dates(week_str))
    state = read_hud_state()
    return [
        {"date": str(day), "content": str(content)}
        for day, content in sorted(state.today_focus.items())
        if str(day) in dates and str(content).strip()
    ]


def _load_last_week_observation(week_str: str) -> str | None:
    previous_start = _week_start(week_str) - timedelta(days=7)
    iso = previous_start.isocalendar()
    previous_week = f"{iso.year}-W{iso.week:02d}"
    path = _weekly_path(previous_week)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(r"### 一个观察\s+(.+?)(?:\n#|\Z)", text, re.DOTALL)
    if not match:
        return None
    return " ".join(line.strip("> ").strip() for line in match.group(1).splitlines() if line.strip())[:120] or None


def _merge_topic_files(into_slug: str, from_slug: str) -> bool:
    topics_dir = get_data_dir() / "topics"
    into_path = topics_dir / f"{into_slug}.md"
    from_path = topics_dir / f"{from_slug}.md"
    if not into_path.exists() or not from_path.exists():
        return False

    into_text = into_path.read_text(encoding="utf-8")
    from_text = from_path.read_text(encoding="utf-8")

    from_entries = _parse_markdown_lines(from_text, "## Entries")
    from_related = _parse_markdown_lines(from_text, "## Related Events")

    merged_text = _append_section_lines(into_text, "## Entries", from_entries)
    merged_text = _append_section_lines(merged_text, "## Related Events", from_related)

    atomic_write_text(into_path, merged_text)
    from_path.unlink()
    return True


def _apply_reconcile_decisions(decisions: list[dict[str, Any]]) -> list[str]:
    merged: list[str] = []
    for item in decisions:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if action != "merge":
            continue
        into_slug = str(item.get("into") or "").strip()
        slug_a = str(item.get("slug_a") or "").strip()
        slug_b = str(item.get("slug_b") or "").strip()
        if not into_slug or not slug_a or not slug_b:
            continue
        from_slug = slug_b if into_slug == slug_a else slug_a
        if _merge_topic_files(into_slug, from_slug):
            merged.append(f"{from_slug}->{into_slug}")
    return merged


def _status_badge(status: str, lifecycle: str) -> str:
    mapping = {
        "new": "新冒头",
        "accelerating": "★加速",
        "revived": "沉睡复活",
        "ongoing": "持续主线",
        "steady": "持平",
        "declining": "退潮",
    }
    if lifecycle == "dormant":
        return "休眠"
    return mapping.get(status, status)


def _build_main_section_lines(
    top_topics: list[dict[str, Any]],
    topic_index_map: dict[str, dict[str, Any]],
    l5_output: dict[str, Any],
) -> list[str]:
    narratives = l5_output.get("narratives") if isinstance(l5_output, dict) else []
    by_slug: dict[str, dict[str, Any]] = {}
    if isinstance(narratives, list):
        for item in narratives:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip()
            if slug:
                by_slug[slug] = item

    lines = ["## 这周的主线", ""]
    for topic in top_topics:
        slug = str(topic.get("slug") or "").strip()
        display = str(topic.get("name") or topic.get("display_name") or slug)
        state = str(topic.get("state") or "in_progress")
        lines.append(f"### {display} ({_exec_state_label(state)})")

        payload = by_slug.get(slug, {})
        narrative = str(payload.get("narrative") or "").strip()
        anchors = payload.get("anchors") if isinstance(payload.get("anchors"), list) else []
        if not narrative:
            fallback_anchor = anchors[0] if anchors else ""
            narrative = f"本周{display}延续已有节奏。{fallback_anchor}".strip()
        lines.append(narrative)
        decisions = [str(item).strip() for item in (payload.get("decisions") or []) if str(item).strip()]
        outputs = [str(item).strip() for item in (payload.get("outputs") or []) if str(item).strip()]
        blockers = [str(item).strip() for item in (payload.get("blockers") or []) if str(item).strip()]
        if decisions:
            lines.append(f"→ 关键决策: {'；'.join(decisions[:3])}")
        if outputs:
            lines.append(f"→ 可见产出: {'；'.join(outputs[:3])}")
        if blockers:
            lines.append(f"→ ⚠️  卡点: {'；'.join(blockers[:3])}")
        lines.append("")
    if len(lines) == 2:
        lines.append("- （本周无可写主题）")
        lines.append("")
    return lines


def _build_explorer_section_lines(l6_output: dict[str, Any]) -> list[str]:
    missed = l6_output.get("dropped_balls") if isinstance(l6_output, dict) else []
    if not missed:
        missed = l6_output.get("missed_balls") if isinstance(l6_output, dict) else []
    observation = l6_output.get("observation") if isinstance(l6_output, dict) else {}

    lines = ["## 这周的回声", "", "### 没接住的球"]
    if isinstance(missed, list) and missed:
        for item in missed[:3]:
            if not isinstance(item, dict):
                continue
            what = str(item.get("what") or "").strip()
            anchor = str(item.get("anchor_link") or "").strip()
            if what:
                lines.append(f"- {what}{(' ' + anchor) if anchor else ''}")
    else:
        lines.append("- （无）")

    lines.extend(["", "### 一个观察"])
    if isinstance(observation, dict):
        text = str(observation.get("text") or "").strip()
        anchor = str(observation.get("anchor_link") or "").strip()
        quote = str(observation.get("anchor_quote") or "").strip()
        if text:
            lines.append(f"{text}{(' ' + anchor) if anchor else ''}")
            if quote:
                lines.append(f"> 证据: {quote}")
        else:
            lines.append("本周笔友没看见值得问的事。")
    else:
        lines.append("本周笔友没看见值得问的事。")

    lines.extend(["", "> [!note] 我的批注", "> (空,M2 写回长期记忆)", ""])
    return lines


def _build_cross_week_section_lines(cross_week_diff: list[dict[str, str]]) -> list[str]:
    lines = ["## 跨周差异", ""]
    if not cross_week_diff:
        lines.extend(["- （本周无显式状态迁移）", ""])
        return lines
    for item in cross_week_diff[:8]:
        topic = str(item.get("topic") or "").strip()
        from_state = str(item.get("from_state") or "").strip()
        to_state = str(item.get("to_state") or "").strip()
        if topic and from_state and to_state:
            lines.append(f"- 跨周状态迁移: {topic} {from_state}->{to_state}")
    lines.append("")
    return lines


def _build_principles_section_lines(principles: list[dict[str, str]]) -> list[str]:
    if not principles:
        return []

    lines = ["## 本周新沉淀原则", ""]
    for item in principles:
        slug = str(item.get("slug") or item.get("principle_id") or "").strip()
        distilled = str(item.get("distilled") or "").strip()
        if not slug:
            continue
        if distilled:
            lines.append(f"- [[{slug}]] — {distilled}")
        else:
            lines.append(f"- [[{slug}]]")
    if len(lines) == 2:
        return []
    lines.append("")
    return lines


def _yaml_scalar(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_unquote(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        try:
            return str(json.loads(token))
        except json.JSONDecodeError:
            return token[1:-1]
    if token.startswith("'") and token.endswith("'") and len(token) >= 2:
        return token[1:-1]
    return token


def _split_frontmatter(markdown: str) -> tuple[list[str] | None, str]:
    lines = str(markdown or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return None, str(markdown or "")
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            frontmatter = lines[1:idx]
            body = "\n".join(lines[idx + 1 :])
            return frontmatter, body
    return None, str(markdown or "")


def _parse_inline_yaml_list(value: str) -> list[str]:
    text = str(value or "").strip()
    if not (text.startswith("[") and text.endswith("]")):
        return [_yaml_unquote(text)] if text else []
    inner = text[1:-1].strip()
    if not inner:
        return []
    return [_yaml_unquote(item) for item in inner.split(",")]


def _remove_tags_from_frontmatter(frontmatter: list[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    existing_tags: list[str] = []
    i = 0
    total = len(frontmatter)
    while i < total:
        line = frontmatter[i]
        stripped = line.strip()
        if stripped.startswith("tags:"):
            inline_value = stripped.split(":", 1)[1].strip()
            if inline_value:
                existing_tags.extend(_parse_inline_yaml_list(inline_value))
            i += 1
            while i < total:
                next_line = frontmatter[i]
                next_stripped = next_line.strip()
                if next_stripped.startswith("- "):
                    existing_tags.append(_yaml_unquote(next_stripped[2:].strip()))
                    i += 1
                    continue
                if next_line.startswith(" ") or next_line.startswith("\t"):
                    i += 1
                    continue
                break
            continue
        kept.append(line)
        i += 1
    return kept, existing_tags


def _dedupe_tags(tags: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        key = str(tag).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _weekly_tags(style: str) -> list[str]:
    return ["weekly", f"weekly/{style}"]


def _weekly_alias(week_str: str) -> str:
    start = _week_start(week_str)
    end = start + timedelta(days=6)
    if current_lang() == "zh":
        date_range = f"{start.month}/{start.day}–{end.month}/{end.day}"
    else:
        date_range = f"{_EN_MONTHS[start.month - 1]} {start.day} – {_EN_MONTHS[end.month - 1]} {end.day}"
    return f"{week_str} ({date_range})"


def _upsert_frontmatter_tags(markdown: str, tags: list[str]) -> str:
    required_tags = _dedupe_tags(tags)
    frontmatter, body = _split_frontmatter(markdown)
    if frontmatter is None:
        lines = [
            "---",
            "tags:",
            *[f"  - {_yaml_scalar(tag)}" for tag in required_tags],
            "---",
            str(markdown or ""),
        ]
        return "\n".join(lines).rstrip()

    kept, existing = _remove_tags_from_frontmatter(frontmatter)
    merged_tags = _dedupe_tags([*existing, *required_tags])
    rebuilt_frontmatter = [
        *kept,
        "tags:",
        *[f"  - {_yaml_scalar(tag)}" for tag in merged_tags],
    ]
    lines = ["---", *rebuilt_frontmatter, "---", body]
    return "\n".join(lines).rstrip()


def _remove_aliases_from_frontmatter(frontmatter: list[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    existing_aliases: list[str] = []
    i = 0
    total = len(frontmatter)
    while i < total:
        line = frontmatter[i]
        stripped = line.strip()
        if stripped.startswith("aliases:"):
            inline_value = stripped.split(":", 1)[1].strip()
            if inline_value:
                existing_aliases.extend(_parse_inline_yaml_list(inline_value))
            i += 1
            while i < total:
                next_line = frontmatter[i]
                next_stripped = next_line.strip()
                if next_stripped.startswith("- "):
                    existing_aliases.append(_yaml_unquote(next_stripped[2:].strip()))
                    i += 1
                    continue
                if next_line.startswith(" ") or next_line.startswith("\t"):
                    i += 1
                    continue
                break
            continue
        kept.append(line)
        i += 1
    return kept, existing_aliases


def _upsert_frontmatter_aliases(markdown: str, aliases: list[str]) -> str:
    required_aliases = _dedupe_tags(aliases)
    if not required_aliases:
        return markdown
    frontmatter, body = _split_frontmatter(markdown)
    if frontmatter is None:
        lines = [
            "---",
            "aliases:",
            *[f"  - {_yaml_scalar(alias)}" for alias in required_aliases],
            "---",
            str(markdown or ""),
        ]
        return "\n".join(lines).rstrip()

    kept, existing = _remove_aliases_from_frontmatter(frontmatter)
    merged_aliases = _dedupe_tags([*existing, *required_aliases])
    rebuilt_frontmatter = [
        *kept,
        "aliases:",
        *[f"  - {_yaml_scalar(alias)}" for alias in merged_aliases],
    ]
    lines = ["---", *rebuilt_frontmatter, "---", body]
    return "\n".join(lines).rstrip()


def _render_weekly_markdown(
    week_str: str,
    top_topics: list[dict[str, Any]],
    topic_index_map: dict[str, dict[str, Any]],
    l5_output: dict[str, Any],
    l6_output: dict[str, Any],
    cross_week_diff: list[dict[str, str]],
    new_principles: list[dict[str, str]],
    *,
    style: str,
    daily_count: int,
    stats: WeeklyRunStats,
    quality_breakdown: QualityBreakdown | None = None,
    quality_history: str = "",
    key_data_section: str = "",
) -> str:
    if style == "plain":
        lines = [f"# 这周 ({week_str})", ""]
        lines.extend(_build_main_section_lines(top_topics, topic_index_map, l5_output))
        lines.extend(_build_cross_week_section_lines(cross_week_diff))
        lines.extend(_build_principles_section_lines(new_principles))
        lines.extend(_build_explorer_section_lines(l6_output))
        lines.extend(["", *_generation_info_lines(stats, quality_breakdown, quality_history)])
        rendered = "\n".join(lines)
    else:
        lines = _render_exec_weekly_markdown(
            week_str,
            top_topics,
            l5_output,
            l6_output,
            cross_week_diff=cross_week_diff,
            new_principles=new_principles,
            daily_count=daily_count,
            stats=stats,
            quality_breakdown=quality_breakdown,
            quality_history=quality_history,
            key_data_section=key_data_section,
        )
        rendered = "\n".join(lines)
    with_tags = _upsert_frontmatter_tags(rendered, _weekly_tags(style))
    return _upsert_frontmatter_aliases(with_tags, [_weekly_alias(week_str)])


def _exec_state_label(state: str) -> str:
    return {
        "completed": "完成",
        "in_progress": "推进中",
        "started": "启动",
        "blocked": "卡住",
    }.get(state, "推进中")


def _exec_state_icon(state: str) -> str:
    return {
        "completed": "✅",
        "in_progress": "🔄",
        "started": "🆕",
        "blocked": "⚠️",
    }.get(state, "🔄")


def _quality_log_path() -> Path:
    return get_data_dir() / "weekly-quality.jsonl"


def _quality_history_text(week_str: str, total: int, *, log_path: Path) -> str:
    history: list[tuple[str, int]] = []
    if log_path.exists():
        with log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                week = str(row.get("week") or "").strip()
                score = row.get("total")
                if not week:
                    continue
                try:
                    numeric = int(score)
                except (TypeError, ValueError):
                    continue
                history.append((week, numeric))
    history = [(week, score) for week, score in history if week != week_str]
    history = history[-2:] + [(week_str, total)]
    if not history:
        return ""
    trail = " → ".join(f"{week.split('-', 1)[-1]}={score}" for week, score in history)
    return f"历史趋势：{trail}  ↗"


def _quality_remark(score: int, ok: str, warn: str) -> str:
    return ok if score >= 100 else warn


def _generation_info_lines(
    stats: WeeklyRunStats,
    quality_breakdown: QualityBreakdown | None,
    quality_history: str,
) -> list[str]:
    if quality_breakdown is None:
        return [
            (
                f"> 生成信息: L4={stats.l4_source} L5={stats.l5_source_label()} L6={stats.l6_source} "
                f"· 共 {stats.llm_calls} 次 LLM 调用 · ${stats.cost_usd:.2f}"
            )
        ]

    lines = [
        f"> 生成信息: 质量 {quality_breakdown.total}/100 · {stats.llm_calls} 次模型调用 · ${stats.cost_usd:.2f} [详情]",
        "<details>",
        "<summary>质量分详情</summary>",
        "",
        "| 维度 | 分数 | 备注 |",
        "|---|---|---|",
        f"| 结构完整性 | {quality_breakdown.structure}/100 | {_quality_remark(quality_breakdown.structure, '✓ 7 段都在', '⚠ 段落结构有缺失')} |",
        f"| 内容覆盖度 | {quality_breakdown.coverage}/100 | {_quality_remark(quality_breakdown.coverage, '✓ 覆盖主线/决策/产出', '⚠ 覆盖项有缺口')} |",
        f"| 文本质感 | {quality_breakdown.texture}/100 | {_quality_remark(quality_breakdown.texture, '✓ 事实与判断组合完整', '⚠ 判断句或叙事顺序不足')} |",
        f"| 客观性溯源 | {quality_breakdown.objectivity}/100 | {_quality_remark(quality_breakdown.objectivity, '✓ 数字/日期/工具名可追溯', '⚠ 存在待核 claim')} |",
        f"| 反模式检查 | {quality_breakdown.antipattern}/100 | {_quality_remark(quality_breakdown.antipattern, '✓ 无空话兜底文案', '⚠ 命中反模式文案')} |",
        "",
    ]
    if quality_history:
        lines.append(quality_history)
    lines.append("</details>")
    return lines


def _render_exec_weekly_markdown(
    week_str: str,
    top_topics: list[dict[str, Any]],
    l5_output: dict[str, Any],
    l6_output: dict[str, Any],
    cross_week_diff: list[dict[str, str]],
    new_principles: list[dict[str, str]],
    *,
    daily_count: int,
    stats: WeeklyRunStats,
    quality_breakdown: QualityBreakdown | None = None,
    quality_history: str = "",
    key_data_section: str = "",
) -> list[str]:
    start = _week_start(week_str)
    end = start + timedelta(days=6)
    narratives = l5_output.get("narratives") if isinstance(l5_output, dict) else []
    by_slug = {
        str(item.get("slug") or ""): item
        for item in narratives
        if isinstance(item, dict) and str(item.get("slug") or "").strip()
    } if isinstance(narratives, list) else {}
    completed = sum(1 for item in top_topics if str(item.get("state") or "") == "completed")
    in_progress = sum(1 for item in top_topics if str(item.get("state") or "") == "in_progress")
    started = sum(1 for item in top_topics if str(item.get("state") or "") == "started")
    total_entries = sum(_weekly_entry_count(item) for item in top_topics)
    tldr = "本周主线集中在" + "、".join(str(item.get("name") or item.get("slug")) for item in top_topics[:2]) if top_topics else "本周没有形成足够主题。"
    lines = [
        f"# 本周工作汇报 ({week_str}, {start.month}/{start.day}-{end.month}/{end.day})",
        "",
        "## TL;DR",
        tldr,
    ]
    if key_data_section.strip():
        lines.extend(["", *key_data_section.strip().splitlines(), ""])
    else:
        lines.extend(
            [
                "",
                "## 关键数据",
                f"- 实质活动主题: {len(top_topics)} 个 ({completed} 完成 · {in_progress} 推进中 · {started} 启动)",
                f"- 已记录天数: {daily_count} / 7",
                "",
            ]
        )
    lines.append("## 本周关键进展")
    for topic in top_topics:
        slug = str(topic.get("slug") or "")
        name = str(topic.get("name") or slug)
        state = str(topic.get("state") or "in_progress")
        payload = by_slug.get(slug, {})
        narrative = str(payload.get("narrative") or "").strip() or f"本周{name}有记录，见 [[{str((topic.get('weekly_entries') or [{'date': start.isoformat()}])[0].get('date'))}]]。"
        decisions = [str(item).strip() for item in (payload.get("decisions") or []) if str(item).strip()]
        outputs = [str(item).strip() for item in (payload.get("outputs") or []) if str(item).strip()]
        blockers = [str(item).strip() for item in (payload.get("blockers") or []) if str(item).strip()]
        meaning = str(payload.get("meaning") or "").strip()
        lines.append(f"### {_exec_state_icon(state)} {name} | {_exec_state_label(state)}")
        lines.append(narrative)
        if meaning:
            lines.append(f"→ 这意味着: {meaning}")
        if decisions:
            lines.append(f"→ 关键决策: {'；'.join(decisions[:3])}")
        if outputs:
            lines.append(f"→ 可见产出: {'；'.join(outputs[:3])}")
        if blockers:
            lines.append(f"→ ⚠️  卡点: {'；'.join(blockers[:3])}")
        lines.append("")
    risks = l6_output.get("risks") if isinstance(l6_output, dict) else []
    valid_risks: list[tuple[str, str, str]] = []
    if isinstance(risks, list):
        for risk in risks[:5]:
            if isinstance(risk, dict):
                r = str(risk.get("risk") or risk.get("text") or risk.get("content") or "").strip()
                i = str(risk.get("impact", "") or "").strip()
                d = str(risk.get("direction", "") or "").strip()
                if r:
                    valid_risks.append((r, i, d))
            else:
                text = str(risk or "").strip()
                if text:
                    valid_risks.append((text, "", ""))
    lines.extend(["## 本周风险", "| # | 风险 | 影响 | 处理方向 |", "|---|---|---|---|"])
    if valid_risks:
        for idx, (r, i, d) in enumerate(valid_risks, start=1):
            lines.append(f"| {idx} | {r} | {i or '—'} | {d or '—'} |")
    else:
        lines.append("| — | （无） | — | — |")
    dropped = l6_output.get("dropped_balls") if isinstance(l6_output, dict) else []
    if not dropped:
        dropped = l6_output.get("missed_balls") if isinstance(l6_output, dict) else []
    valid_dropped: list[str] = []
    if isinstance(dropped, list):
        for item in dropped[:5]:
            if isinstance(item, dict):
                anchor = str(item.get("anchor_link", "") or "").strip()
                what = str(item.get("what", "") or "").strip()
                content = str(item.get("content", "") or "").strip()
                date = str(item.get("date", "") or "").strip()
                if not anchor and date:
                    anchor = f"[[{date}]]"
                if not what and content:
                    what = content
                text = f"{anchor} {what}".strip()
                if what or anchor:
                    valid_dropped.append(text)
            else:
                text = str(item or "").strip()
                if text:
                    valid_dropped.append(text)
    lines.extend(["", "## 没接住的球"])
    if valid_dropped:
        for line in valid_dropped:
            lines.append(f"- {line}")
    else:
        lines.append("- （无）")
    observation = l6_output.get("observation") if isinstance(l6_output, dict) else {}
    lines.extend(["", "## 一个观察"])
    if isinstance(observation, dict) and str(observation.get("text") or "").strip():
        text = str(observation.get("text") or "").strip()
        anchor = str(observation.get("anchor_link") or "").strip()
        quote = str(observation.get("anchor_quote") or "").strip()
        lines.append(f"{text}{(' ' + anchor) if anchor else ''}".strip())
        if quote:
            lines.append(f"- 证据: {quote}")
    else:
        lines.append("- 本周笔友没看见值得问的事。")
    lines.extend(["", "## 跨周差异"])
    if cross_week_diff:
        for item in cross_week_diff[:8]:
            topic = str(item.get("topic") or "").strip()
            from_state = str(item.get("from_state") or "").strip()
            to_state = str(item.get("to_state") or "").strip()
            if topic and from_state and to_state:
                lines.append(f"- 跨周状态迁移: {topic} {from_state}->{to_state}")
    else:
        lines.append("- （本周无显式状态迁移）")
    principle_section = _build_principles_section_lines(new_principles)
    if principle_section:
        lines.extend(["", *principle_section])
    anchors = _next_week_anchors(top_topics, l6_output)
    anchor_lines = [f"- {item}" for item in anchors] if anchors else ["- （无）"]
    lines.extend(["", "## 下周锚点", *anchor_lines, "", "---", *_generation_info_lines(stats, quality_breakdown, quality_history)])
    return lines


def _next_week_anchors(top_topics: list[dict[str, Any]], l6_output: dict[str, Any]) -> list[str]:
    explicit = []
    if isinstance(l6_output, dict):
        for item in l6_output.get("next_week_anchors") or l6_output.get("anchors") or []:
            text = str(item.get("text") if isinstance(item, dict) else item or "").strip()
            if text:
                explicit.append(text)
    if explicit:
        return explicit[:5]
    anchors = []
    for topic in top_topics[:4]:
        name = str(topic.get("name") or topic.get("slug") or "主题")
        state = str(topic.get("state") or "")
        if state == "blocked":
            anchors.append(f"解卡 {name}")
        elif state == "in_progress":
            anchors.append(f"推进 {name}")
        elif state == "started":
            anchors.append(f"承接 {name}")
    return anchors[:5]


def _is_valid_l4_output(value: Any) -> bool:
    raw = value.get("topics") if isinstance(value, dict) else value
    if not isinstance(raw, list):
        return False
    for item in raw:
        if not isinstance(item, dict):
            return False
        if not str(item.get("slug") or "").strip():
            return False
        if str(item.get("state") or "").strip() not in {"started", "in_progress", "completed", "blocked"}:
            return False
    return True


def _is_valid_l5_output(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if not str(value.get("slug") or "").strip():
        return False
    if not str(value.get("narrative") or "").strip():
        return False
    anchors = value.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        return False
    return True


def _is_valid_l6_output(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    observation = value.get("observation")
    if not isinstance(observation, dict):
        return False
    text = str(observation.get("text") or "").strip()
    return bool(text)


def _append_log(record: dict[str, Any]) -> None:
    log_path = get_data_dir() / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _weekly_path(week_str: str) -> Path:
    sink = resolve_active_sink(Config.load(), persist=False)
    path = sink.output_dir / "Weekly" / f"{week_str}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _mock_l4_output(payload: dict[str, Any]) -> dict[str, Any]:
    topics = []
    for item in payload.get("topics") or []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        topics.append(
            {
                "slug": slug,
                "name": str(item.get("name") or slug),
                "state": str(item.get("state") or "in_progress"),
                "weekly_entries": [entry for entry in (item.get("weekly_entries") or []) if isinstance(entry, dict)],
            }
        )
    return topics


def _mock_l5_output(payload: dict[str, Any]) -> dict[str, Any]:
    topic = payload.get("topic") if isinstance(payload.get("topic"), dict) else {}
    slug = str(topic.get("slug") or "").strip()
    display = str(topic.get("name") or topic.get("display_name") or slug).strip()
    entries = topic.get("weekly_entries") or []
    first = entries[0]["date"] if entries else "2026-01-01"
    second = entries[1]["date"] if len(entries) > 1 else first
    return {
        "slug": slug,
        "narrative": f"本周{display}继续推进，改了关键路径并把细节记下。 [[{first}]] 到 [[{second}]] 形成了可见变化，这是定调。",
        "anchors": [f"[[{first}]]", f"[[{second}]]"],
        "decisions": [f"确认{display}作为本周主题"],
        "outputs": [f"{display}形成周记录"],
        "blockers": [],
    }


def _mock_l6_output(payload: dict[str, Any]) -> dict[str, Any]:
    dailies = payload.get("weekly_dailies") or []
    first_day = dailies[0]["date"] if dailies else "2026-01-01"
    first_body = str(dailies[0].get("content_full") or "") if dailies else ""
    quote = first_body[:60] if first_body else "本周持续推进同一主线"
    hud_inputs = payload.get("hud_inputs_this_week") or []
    missed_balls = (
        [{"what": "周一你说想 收敛主线,后面没看到", "anchor_link": f"[[{first_day}]]"}] if hud_inputs else []
    )
    return {
        "dropped_balls": missed_balls,
        "observation": {
            "text": "最近同一条线多次返工，是否能再拆小一步？",
            "anchor_link": f"[[{first_day}]]",
            "anchor_quote": quote,
        },
        "risks": [],
    }


def _mock_l7_output(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = [item for item in (payload.get("candidates") or []) if isinstance(item, dict)]
    if not candidates:
        return {"derived_from": None, "confidence": 0.0, "reason": "no_candidates"}
    target = candidates[0]
    return {
        "derived_from": str(target.get("slug") or ""),
        "confidence": 0.86,
        "reason": "mock_high_confidence",
    }


def _legacy_template_fallback(topic: dict[str, Any], reason: str) -> dict[str, Any]:
    slug = str(topic.get("slug") or "").strip()
    name = str(topic.get("name") or slug).strip()
    entries = [entry for entry in (topic.get("weekly_entries") or []) if isinstance(entry, dict)]
    first_date = str(entries[0].get("date") or _week_start("2026-W01").isoformat()) if entries else "2026-01-01"
    first_summary = str(entries[0].get("narrative_one_line") or name).strip() if entries else name
    return {
        "slug": slug,
        "narrative": f"### {name}\n{first_summary} [[{first_date}]]（LLM 失败-机械合成版）",
        "anchors": [f"[[{first_date}]]"],
        "decisions": ["（LLM 失败-机械合成版）未抽取到关键决策"],
        "outputs": ["（LLM 失败-机械合成版）未抽取到可见产出"],
        "blockers": [f"LLM 失败: {reason}"] if reason else [],
    }


def _fallback_l5_topic(
    topic: dict[str, Any],
    reason: str,
    *,
    daily_summaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if isinstance(daily_summaries, list) and daily_summaries:
        degraded = generate_degraded_topic(topic, daily_summaries, reason)
        if str(degraded.get("narrative") or "").strip():
            return degraded
    return _legacy_template_fallback(topic, reason)


def _normalize_l5_topic_output(
    output: Any,
    topic: dict[str, Any],
    *,
    daily_summaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    fallback = _fallback_l5_topic(topic, "", daily_summaries=daily_summaries)
    if isinstance(output, str):
        parsed = _parse_llm_json_robust(output)
        output = parsed if parsed is not None else output
    if not isinstance(output, dict):
        return fallback
    slug = str(output.get("slug") or topic.get("slug") or "").strip()
    entries = [entry for entry in (topic.get("weekly_entries") or []) if isinstance(entry, dict)]
    anchor = f"[[{str(entries[0].get('date') or '2026-01-01')}]]" if entries else "[[2026-01-01]]"
    narrative = str(output.get("narrative") or fallback["narrative"]).strip()
    if "[[" not in narrative:
        narrative = f"{narrative} {anchor}".strip()
    return {
        "slug": slug,
        "narrative": narrative,
        "anchors": [str(item) for item in (output.get("anchors") or [anchor]) if str(item).strip()],
        "decisions": [str(item) for item in (output.get("decisions") or []) if str(item).strip()],
        "outputs": [str(item) for item in (output.get("outputs") or []) if str(item).strip()],
        "blockers": [str(item) for item in (output.get("blockers") or []) if str(item).strip()],
    }


def _weekday_cn(date_text: str) -> str:
    labels = ["一", "二", "三", "四", "五", "六", "日"]
    try:
        day = date_cls.fromisoformat(date_text)
    except ValueError:
        return "一"
    return labels[day.weekday()]


def _input_matched_after(input_text: str, after_text: str) -> bool:
    words = [item for item in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,}", input_text) if len(item) >= 2]
    if not words:
        return False
    return any(word in after_text for word in words[:8])


def _fallback_l6(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    dailies = [item for item in (payload.get("weekly_dailies") or []) if isinstance(item, dict)]
    by_date = {str(item.get("date") or ""): str(item.get("content_full") or "") for item in dailies}
    dropped: list[dict[str, str]] = []
    for hud in payload.get("hud_inputs_this_week") or []:
        if not isinstance(hud, dict):
            continue
        date_text = str(hud.get("date") or "").strip()
        content = str(hud.get("content") or "").strip()
        later = "\n".join(text for day, text in sorted(by_date.items()) if day >= date_text)
        if content and not _input_matched_after(content, later):
            action = content[:12]
            dropped.append({"what": f"周{_weekday_cn(date_text)}你说想 {action},后面没看到", "anchor_link": f"[[{date_text}]]"})
    quote = ""
    anchor = None
    for day, text in sorted(by_date.items()):
        stripped = " ".join(line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))
        if len(stripped) >= 10:
            quote = stripped[:80]
            anchor = f"[[{day}]]"
            break
    return {
        "dropped_balls": dropped[:3],
        "observation": {
            "text": "本周笔友没看见值得问的事。",
            "anchor_link": anchor,
            "anchor_quote": quote or None,
        },
        "risks": [{"risk": "L6 LLM 失败", "impact": "探索者内容降级", "direction": "保留规则输出并下次重试"}] if reason else [],
    }


def _normalize_l6_output(output: Any) -> dict[str, Any]:
    if isinstance(output, str):
        parsed = _parse_llm_json_robust(output)
        output = parsed if parsed is not None else output
    if not isinstance(output, dict):
        return {"dropped_balls": [], "missed_balls": [], "observation": {}, "risks": []}
    dropped = output.get("dropped_balls")
    if not isinstance(dropped, list):
        dropped = output.get("missed_balls") if isinstance(output.get("missed_balls"), list) else []
    normalized = dict(output)
    normalized["dropped_balls"] = [item for item in dropped if isinstance(item, dict)]
    normalized["missed_balls"] = list(normalized["dropped_balls"])
    risks = normalized.get("risks")
    normalized["risks"] = [item for item in risks if isinstance(item, (dict, str))] if isinstance(risks, list) else []
    return normalized


def _ensure_cross_week_element_present(
    l5_output: dict[str, Any],
    *,
    cross_week_diff: list[dict[str, str]],
    key_decisions: list[dict[str, str]],
    visible_outputs: list[dict[str, str]],
) -> dict[str, Any]:
    narratives = l5_output.get("narratives") if isinstance(l5_output, dict) else None
    if not isinstance(narratives, list) or not narratives:
        return l5_output

    corpus = "\n".join(
        [
            str(item.get("narrative") or "")
            + "\n"
            + "\n".join(str(v) for v in (item.get("decisions") or []))
            + "\n"
            + "\n".join(str(v) for v in (item.get("outputs") or []))
            for item in narratives
            if isinstance(item, dict)
        ]
    )
    if any(str(item.get("text") or "").strip() and str(item.get("text")) in corpus for item in key_decisions):
        return l5_output
    if any(str(item.get("text") or "").strip() and str(item.get("text")) in corpus for item in visible_outputs):
        return l5_output
    if "跨周状态迁移" in corpus:
        return l5_output

    target = narratives[0]
    if not isinstance(target, dict):
        return l5_output
    patched = dict(target)
    decisions = [str(item) for item in (patched.get("decisions") or []) if str(item).strip()]
    outputs = [str(item) for item in (patched.get("outputs") or []) if str(item).strip()]
    if cross_week_diff:
        first = cross_week_diff[0]
        decisions.append(
            f"跨周状态迁移: {str(first.get('topic') or '').strip()} {str(first.get('from_state') or '').strip()}->{str(first.get('to_state') or '').strip()}"
        )
    elif key_decisions:
        decisions.append(str(key_decisions[0].get("text") or "").strip())
    elif visible_outputs:
        outputs.append(str(visible_outputs[0].get("text") or "").strip())
    patched["decisions"] = decisions[:3]
    patched["outputs"] = outputs[:3]

    updated = list(narratives)
    updated[0] = patched
    result = dict(l5_output)
    result["narratives"] = updated
    return result


def _install_mock_backend(gateway: ModelGateway) -> None:
    fail_budget = int(os.environ.get("MOCK_LLM_FAILS", "0") or 0)
    delay_sec = float(os.environ.get("MOCK_WEEKLY_DELAY_SEC", "0") or 0.0)
    fail_counter = {"count": 0}
    invalid_first_all = int(os.environ.get("MOCK_WEEKLY_INVALID_FIRST", "0") or 0)
    invalid_first_l5 = int(os.environ.get("MOCK_WEEKLY_INVALID_FIRST_L5", str(invalid_first_all)) or 0)
    invalid_first_l6 = int(os.environ.get("MOCK_WEEKLY_INVALID_FIRST_L6", str(invalid_first_all)) or 0)
    invalid_counter_l5 = {"count": 0}
    invalid_counter_l6 = {"count": 0}

    def fake_call_capability_backend(**kwargs):
        if fail_counter["count"] < fail_budget:
            fail_counter["count"] += 1
            raise RuntimeError("mock llm forced failure")

        if delay_sec > 0:
            capability = str(kwargs.get("prompt") or "").splitlines()[0]
            if "L5_weekly_main_narrative" in capability or "L6_explorer" in capability:
                time.sleep(delay_sec)

        prompt = str(kwargs.get("prompt") or "")
        capability, payload = _extract_prompt_payload(prompt)
        if capability == "L4_weekly_reconcile":
            body = json.dumps(_mock_l4_output(payload), ensure_ascii=False)
        elif capability == "L5_weekly_main_narrative":
            if invalid_counter_l5["count"] < invalid_first_l5:
                invalid_counter_l5["count"] += 1
                mocked = _mock_l5_output(payload)
                if isinstance(mocked, dict) and isinstance(mocked.get("narrative"), str):
                    mocked = dict(mocked)
                    mocked["narrative"] = str(mocked["narrative"]).replace("改了", "高效改了", 1)
                body = json.dumps(mocked, ensure_ascii=False)
            else:
                body = json.dumps(_mock_l5_output(payload), ensure_ascii=False)
        elif capability == "L6_explorer":
            if invalid_counter_l6["count"] < invalid_first_l6:
                invalid_counter_l6["count"] += 1
                mocked = _mock_l6_output(payload)
                if isinstance(mocked, dict):
                    observation = mocked.get("observation")
                    if isinstance(observation, dict):
                        observation = dict(observation)
                        observation["text"] = str(observation.get("text") or "").replace("是否能", "是否可以", 1)
                        mocked = dict(mocked)
                        mocked["observation"] = observation
                body = json.dumps(mocked, ensure_ascii=False)
            else:
                body = json.dumps(_mock_l6_output(payload), ensure_ascii=False)
        elif capability == "L7_anchor_derivation":
            body = json.dumps(_mock_l7_output(payload), ensure_ascii=False)
        else:
            body = json.dumps({"ok": True}, ensure_ascii=False)
        return {"text": body, "in_tokens": 120, "out_tokens": 40, "cost_usd": 0.0}

    gateway._call_capability_backend = fake_call_capability_backend  # type: ignore[attr-defined,assignment]


def _load_gateway() -> ModelGateway:
    cfg = Config.load()
    gateway = load_model_gateway(cfg)
    if os.environ.get("MOCK_LLM") == "1":
        _install_mock_backend(gateway)
    return gateway


def _status_for_prompt(topic: TopicStatusSnapshot) -> str:
    if topic.status in {"new", "accelerating", "steady", "declining", "revived", "ongoing"}:
        return topic.status
    if topic.lifecycle_status == "emerging":
        return "new"
    return "steady"


def _set_weekly_notice(week_str: str, message: str) -> None:
    payload = {"week": week_str, "message": message}
    set_state("weekly_notice", json.dumps(payload, ensure_ascii=False))


def _clear_weekly_notice() -> None:
    set_state("weekly_notice", "")


def _parse_iso_date(value: str) -> date_cls | None:
    try:
        return date_cls.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def _is_valid_l7_output(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    derived = value.get("derived_from")
    confidence = value.get("confidence")
    if derived is not None and not isinstance(derived, str):
        return False
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return False
    return True


def _home_obsidian_vault_path() -> Path:
    home_cfg = Path.home() / ".keypulse" / "config.toml"
    if home_cfg.exists():
        try:
            with home_cfg.open("rb") as handle:
                payload = tomllib.load(handle)
            obsidian_cfg = payload.get("obsidian") if isinstance(payload, dict) else {}
            if isinstance(obsidian_cfg, dict):
                configured = str(obsidian_cfg.get("vault_path") or "").strip()
                if configured:
                    return Path(configured).expanduser()
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return Path.home() / "Go" / "Knowledge"


def _replace_anchor_display_mentions(summary: str, *, self_slug: str, candidates: list[tuple[str, ...]]) -> str:
    ranked = sorted(
        [
            (
                str(item[0]),
                str(item[1]),
                str(item[2] if len(item) >= 3 else anchor_note_filename(str(item[0]), str(item[1])).removesuffix(".md")),
            )
            for item in candidates
            if isinstance(item, tuple) and len(item) >= 2 and str(item[0]).strip() and str(item[1]).strip()
        ],
        key=lambda item: len(item[0]),
        reverse=True,
    )

    def _replace_plain(text: str) -> str:
        replaced = text
        for display, slug, target in ranked:
            if slug == self_slug:
                continue
            if not display or display not in replaced:
                continue
            replaced = replaced.replace(display, f"[[{target}]]")
        return replaced

    source = str(summary or "")
    parts: list[str] = []
    cursor = 0
    for match in _WIKILINK_RE.finditer(source):
        parts.append(_replace_plain(source[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(_replace_plain(source[cursor:]))
    return "".join(parts)


def _postprocess_anchor_graph(week_str: str, *, gateway: ModelGateway, stats: WeeklyRunStats) -> dict[str, int]:
    anchors = load_weekly_anchors(week_str)
    if not anchors:
        return {"derived_updates": 0, "wikilink_updates": 0}

    week_start = _week_start(week_str)
    week_end = week_start + timedelta(days=6)
    known_slugs = {str(anchor.slug).strip() for anchor in anchors if str(anchor.slug).strip()}

    new_anchors = []
    old_anchors = []
    for anchor in anchors:
        started = _parse_iso_date(str(anchor.started))
        if started is None:
            continue
        if week_start <= started <= week_end:
            new_anchors.append(anchor)
        elif started < week_start:
            old_anchors.append(anchor)

    derived_updates = 0
    for child in new_anchors:
        if str(child.derived_from or "").strip():
            continue
        if not old_anchors:
            break
        candidates = [
            {
                "slug": str(parent.slug),
                "display": str(parent.display or parent.slug),
                "started": str(parent.started or ""),
                "latest_summary": str((parent.timeline_entries or [{}])[-1].get("summary") or ""),
            }
            for parent in old_anchors
        ]
        l7_input = {
            "week": week_str,
            "child": {
                "slug": str(child.slug),
                "display": str(child.display or child.slug),
                "started": str(child.started or ""),
                "timeline": [dict(item) for item in (child.timeline_entries or []) if isinstance(item, dict)][-5:],
            },
            "candidates": candidates,
        }
        l7_prompt = _build_prompt(
            "你要判断新主题是否由某个旧主题派生。只输出 JSON："
            '{"derived_from": "<slug 或 null>", "confidence": <0-1>, "reason": "<一句话>"}。'
            "如果不确定，derived_from=null 且 confidence<0.8。",
            "L7_anchor_derivation",
            l7_input,
        )
        l7_result = _call_weekly_llm(
            gateway=gateway,
            week_str=week_str,
            level="L7",
            capability="L7_anchor_derivation",
            prompt=l7_prompt,
            input_data=l7_input,
            cache_key=_sha1_json(l7_input),
            attempts=2,
            stats=stats,
            validator=_is_valid_l7_output,
        )
        if not isinstance(l7_result.content, dict):
            continue
        derived_slug = str(l7_result.content.get("derived_from") or "").strip()
        confidence = float(l7_result.content.get("confidence") or 0.0)
        if confidence < 0.8:
            continue
        if not derived_slug or derived_slug not in known_slugs or derived_slug == str(child.slug):
            continue
        child.derived_from = derived_slug
        derived_updates += 1

    display_candidates: list[tuple[str, str, str]] = []
    for anchor in anchors:
        display = str(anchor.display or "").strip()
        slug = str(anchor.slug or "").strip()
        if not display or not slug:
            continue
        filename = anchor_note_filename(display, slug)
        target = filename.removesuffix(".md")
        if not target:
            continue
        display_candidates.append((display, slug, target))
    display_candidates.sort(key=lambda item: len(item[0]), reverse=True)

    wikilink_updates = 0
    for anchor in anchors:
        timeline_entries = [dict(item) for item in (anchor.timeline_entries or []) if isinstance(item, dict)]
        next_rows: list[dict[str, Any]] = []
        for row in timeline_entries:
            before = str(row.get("summary") or "")
            after = _replace_anchor_display_mentions(before, self_slug=str(anchor.slug), candidates=display_candidates)
            if after != before:
                wikilink_updates += 1
            row["summary"] = after
            next_rows.append(row)
        anchor.timeline_entries = next_rows

    if derived_updates > 0 or wikilink_updates > 0:
        save_weekly_anchors(week_str, anchors)

    anchor_filename_by_slug: dict[str, str] = {}
    for anchor in anchors:
        slug = str(anchor.slug or "").strip()
        if not slug:
            continue
        anchor_filename_by_slug[slug] = anchor_note_filename(str(anchor.display or ""), slug).removesuffix(".md")

    vault_path = _home_obsidian_vault_path()
    for anchor in anchors:
        try:
            write_anchor_note(anchor, vault_path=vault_path, anchor_filename_by_slug=anchor_filename_by_slug)
        except OSError:
            continue

    return {
        "derived_updates": derived_updates,
        "wikilink_updates": wikilink_updates,
    }


def run_weekly(week_str: str, *, style: str = "exec") -> str:
    if style not in {"plain", "exec"}:
        raise ValueError(f"invalid weekly style: {style}")
    with RunRecorder(date_str=week_str, kind="weekly", trigger="manual") as recorder:
        return _run_weekly_recorded(week_str, style=style, recorder=recorder)


def _run_weekly_recorded(week_str: str, *, style: str, recorder: RunRecorder) -> str:
    run_started = datetime.now(timezone.utc)
    stats = WeeklyRunStats(l5_sources=[])

    def _record_weekly_llm_degraded(result: WeeklyLLMResult) -> None:
        if result.source == "degraded":
            recorder.set_degraded("weekly_llm_degraded", kind=result.error_kind or "unknown")

    with recorder.stage("load_rows"):
        daily_summaries = _load_week_daily_summaries(week_str)
        recorder.set_input_count(sum(int(item.get("event_count") or 0) for item in daily_summaries if isinstance(item, dict)))

    if len(daily_summaries) < _WEEKLY_MIN_DAILY_COUNT:
        recorder.set_output_quality("degraded", degraded_reason="low_event_count")
        _set_weekly_notice(week_str, "本周数据不足，周报跳过")
        _append_log(
            {
                "ts": run_started.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "capability": "weekly_orchestrator",
                "week": week_str,
                "decision": "skip_low_daily_count",
                "daily_count": len(daily_summaries),
            }
        )
        return ""

    _clear_weekly_notice()

    profile = read_profile(Path.home() / ".keypulse" / "profile.toml")
    profile_dict = asdict(profile) if profile is not None else {"work_type": "general", "region": "CN", "religion": ""}
    topics_index = _load_topic_index()
    weekly_history = _build_weekly_history(topics_index, week_str)
    historical_avg = _historical_daily_avg_from_history(weekly_history, lookback_weeks=4)
    week_dates = _week_dates(week_str)
    holiday_ctx = build_holiday_context(
        week_str=week_str,
        week_dates=week_dates,
        daily_event_counts=_daily_event_counts(daily_summaries),
        historical_avg=historical_avg,
        region=str(profile_dict.get("region") or "CN"),
        religion=str(profile_dict.get("religion") or ""),
    )

    previous_start = _week_start(week_str) - timedelta(days=7)
    previous_iso = previous_start.isocalendar()
    previous_week = f"{previous_iso.year}-W{previous_iso.week:02d}"
    previous_week_snapshot = _week_topic_snapshot(previous_week)
    dims = compute_five_dimensions(
        daily_summaries=daily_summaries,
        previous_week_snapshot=previous_week_snapshot,
        profile=profile_dict,
    )
    key_data_section = render_key_data_section(dims, style)

    gateway = _load_gateway()

    merged_week_snapshot = merge_topic_status_snapshots(
        [
            daily.get("topic_status_snapshot")
            for daily in daily_summaries
            if isinstance(daily.get("topic_status_snapshot"), dict)
        ]
    )
    cross_week_diff = _cross_week_state_diff(week_str, merged_week_snapshot)
    key_decisions, visible_outputs, tagged_blockers = _extract_weekly_signals(week_str)

    l4_fallback_topics = _fallback_l4_topics(daily_summaries)
    l4_input = {
        "scope_week": week_str,
        "daily_topic_status_snapshots": [
            {
                "date": str(daily.get("date") or ""),
                "topic_status_snapshot": daily.get("topic_status_snapshot") or {},
            }
            for daily in daily_summaries
        ],
        "topics": l4_fallback_topics,
        "event_counts": {str(topic.get("slug") or ""): _weekly_entry_count(topic) for topic in l4_fallback_topics},
        "cross_week_diff": cross_week_diff,
        "key_decisions": key_decisions,
        "visible_outputs": visible_outputs,
    }
    l4_spec = load_prompt("L4_weekly_reconcile")
    l4_prompt = _build_prompt(l4_spec.body, "L4_weekly_reconcile", l4_input)
    l4_key = _sha1_json(l4_input)
    l4_result = _call_weekly_llm(
        gateway=gateway,
        week_str=week_str,
        level="L4",
        capability="L4_weekly_reconcile",
        prompt=l4_prompt,
        input_data=l4_input,
        cache_key=l4_key,
        attempts=3,
        stats=stats,
        validator=_is_valid_l4_output,
    )
    _record_weekly_llm_degraded(l4_result)
    stats.l4_source = l4_result.source
    candidate_topics = (
        _normalize_l4_output(l4_result.content, daily_summaries) if l4_result.content is not None else list(l4_fallback_topics)
    )
    candidate_topics.sort(
        key=lambda item: (
            {"completed": 0, "in_progress": 1, "blocked": 2, "started": 3}.get(str(item.get("state") or ""), 9),
            -_weekly_entry_count(item),
            str(item.get("slug") or ""),
        )
    )
    top_topics = candidate_topics[:8]
    if not cross_week_diff and top_topics:
        first_topic = top_topics[0]
        cross_week_diff = [
            {
                "topic": str(first_topic.get("name") or first_topic.get("slug") or "主题"),
                "from_state": "started",
                "to_state": str(first_topic.get("state") or "in_progress"),
            }
        ]

    l6_input = {
        "scope_week": week_str,
        "weekly_dailies": [
            {
                "date": str(daily.get("date") or ""),
                "content_full": _read_daily_markdown(str(daily.get("date") or "")) or "\n".join(
                    str(cluster.get("narrative_one_line") or "") for cluster in (daily.get("clusters") or []) if isinstance(cluster, dict)
                ),
            }
            for daily in daily_summaries
        ],
        "topic_status": [
            {
                "slug": str(topic.get("slug") or ""),
                "display_name": str(topic.get("name") or topic.get("slug") or ""),
                "status": _state_status_for_validator(str(topic.get("state") or "")),
                "weekly_count": _weekly_entry_count(topic),
                "last_week_count": 0,
            }
            for topic in top_topics
        ],
        "hud_inputs_this_week": _load_hud_inputs_for_week(week_str),
        "mainline_sections": [],
        "last_week_observation_text": _load_last_week_observation(week_str),
        "cross_week_diff": cross_week_diff,
        "key_decisions": key_decisions,
        "visible_outputs": visible_outputs,
    }

    dailies_text = "\n\n".join(
        "\n".join(
            [
                f"[[{str(item.get('date') or '').strip()}]]",
                str(item.get("content_full") or ""),
            ]
        )
        for item in (l6_input.get("weekly_dailies") or [])
        if isinstance(item, dict)
    )
    hud_input_dates = [
        str(item.get("date") or "").strip()
        for item in (l6_input.get("hud_inputs_this_week") or [])
        if isinstance(item, dict) and str(item.get("date") or "").strip()
    ]
    last_week_observations = (
        [str(l6_input.get("last_week_observation_text") or "").strip()]
        if str(l6_input.get("last_week_observation_text") or "").strip()
        else []
    )

    l5_spec = load_prompt("L5_weekly_main_narrative")
    holiday_system_injection = build_holiday_system_injection(holiday_ctx)
    l5_spec_body = (
        "\n".join(["[HOLIDAY_CONTEXT]", holiday_system_injection, "", l5_spec.body]).strip()
        if holiday_system_injection
        else l5_spec.body
    )
    l5_items: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(top_topics)))) as executor:
        futures = {}
        for topic in top_topics:
            topic_cross_week_diff = [
                item
                for item in cross_week_diff
                if str(item.get("topic") or "").strip()
                and _signal_matches_topic(str(item.get("topic") or ""), topic)
            ][:3]
            topic_key_decisions = _signals_for_topic(key_decisions, topic, limit=3)
            topic_visible_outputs = _signals_for_topic(visible_outputs, topic, limit=3)
            topic_blockers = _signals_for_topic(tagged_blockers, topic, limit=3)
            daily_signals = _collect_daily_topic_signals(topic, daily_summaries)
            l5_input = {
                "scope_week": week_str,
                "topic": topic,
                "evidence": _topic_evidence(topic, daily_summaries),
                "previous_week_narrative": None,
                "cross_week_diff": topic_cross_week_diff,
                "key_decisions": topic_key_decisions,
                "visible_outputs": topic_visible_outputs,
                "tagged_blockers": topic_blockers,
                "daily_decisions": daily_signals["daily_decisions"],
                "daily_shipped": daily_signals["daily_shipped"],
                "daily_anchor_states": daily_signals["daily_anchor_states"],
            }
            l5_prompt = _build_prompt(l5_spec_body, "L5_weekly_main_narrative", l5_input)
            l5_key = _sha1_json(l5_input)

            def run_l5(payload=l5_input, prompt=l5_prompt, key=l5_key, topic_payload=topic):
                return _call_weekly_llm(
                    gateway=gateway,
                    week_str=week_str,
                    level="L5",
                    capability="L5_weekly_main_narrative",
                    prompt=prompt,
                    input_data=payload,
                    cache_key=key,
                    attempts=3,
                    stats=stats,
                    validator=_is_valid_l5_output,
                )

            futures[executor.submit(run_l5)] = topic
        for future in as_completed(futures):
            topic = futures[future]
            result = future.result()
            _record_weekly_llm_degraded(result)
            if stats.l5_sources is not None:
                stats.l5_sources.append(result.source)
            if result.content is None:
                l5_items.append(_fallback_l5_topic(topic, result.reason, daily_summaries=daily_summaries))
            else:
                l5_items.append(_normalize_l5_topic_output(result.content, topic, daily_summaries=daily_summaries))
    l5_items.sort(key=lambda item: [str(topic.get("slug") or "") for topic in top_topics].index(str(item.get("slug") or "")) if str(item.get("slug") or "") in [str(topic.get("slug") or "") for topic in top_topics] else 999)
    l5_output_dict = {"narratives": l5_items}
    l5_output_dict = _ensure_cross_week_element_present(
        l5_output_dict,
        cross_week_diff=cross_week_diff,
        key_decisions=key_decisions,
        visible_outputs=visible_outputs,
    )
    l6_input["mainline_sections"] = [
        {"slug": str(item.get("slug") or ""), "narrative": str(item.get("narrative") or "")}
        for item in l5_items
    ]

    l6_spec = load_prompt("L6_explorer")
    l6_prompt = _build_prompt(l6_spec.body, "L6_explorer", l6_input)
    l6_key = _stable_l6_cache_key(week_str, l5_items, dailies_text, profile_dict)
    l6_result = _call_weekly_llm(
        gateway=gateway,
        week_str=week_str,
        level="L6",
        capability="L6_explorer",
        prompt=l6_prompt,
        input_data=l6_input,
        cache_key=l6_key,
        attempts=3,
        stats=stats,
        validator=_is_valid_l6_output,
    )
    _record_weekly_llm_degraded(l6_result)
    stats.l6_source = l6_result.source
    l6_output_dict = _normalize_l6_output(l6_result.content if l6_result.content is not None else _fallback_l6(l6_input, l6_result.reason))
    sink = resolve_active_sink(Config.load(), persist=False)
    new_principles = list_week_principles(week_str=week_str, vault_path=sink.output_dir)
    validator_attempts = 0
    validator_failures: list[ValidationFailure] = []
    blocking_failures: list[ValidationFailure] = []

    while True:
        rendered_for_validation = _render_weekly_markdown(
            week_str,
            top_topics,
            {},
            l5_output_dict,
            l6_output_dict,
            cross_week_diff,
            new_principles,
            style=style,
            daily_count=len(daily_summaries),
            stats=stats,
            key_data_section=key_data_section,
        )
        validator_failures = validate_weekly_output(
            style=style,
            rendered_markdown=rendered_for_validation,
            dailies_corpus=dailies_text,
            hud_input_dates=hud_input_dates,
            topic_status_snapshot=merged_week_snapshot,
            week_template=holiday_ctx.template,
        )
        blocking_failures = [item for item in validator_failures if item.rule != "claim_unverified"]
        if not blocking_failures or validator_attempts >= _WEEKLY_VALIDATOR_MAX_RETRIES:
            break

        validator_attempts += 1
        feedback = _format_weekly_validation_feedback(blocking_failures)
        needs_l5 = any(
            item.rule.startswith("mainline_") or item.field in {"texture", "structure", "antipattern"}
            for item in blocking_failures
        )
        needs_l6 = any(item.rule.startswith("observation_") or item.rule.startswith("dropped_ball_") for item in blocking_failures)

        if needs_l6:
            retry_result = _call_weekly_llm(
                gateway=gateway,
                week_str=week_str,
                level="L6",
                capability="L6_explorer",
                prompt=l6_prompt + "\n\n" + feedback,
                input_data=l6_input,
                cache_key=f"{l6_key}-retry-{validator_attempts}",
                attempts=3,
                stats=stats,
                validator=_is_valid_l6_output,
            )
            _record_weekly_llm_degraded(retry_result)
            stats.l6_source = retry_result.source
            l6_output_dict = _normalize_l6_output(
                retry_result.content if retry_result.content is not None else _fallback_l6(l6_input, retry_result.reason)
            )
            continue
        if needs_l5:
            break
        break

    weekly_outcome = "ok"
    if blocking_failures:
        weekly_outcome = "partial"
        _append_log(
            {
                "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "capability": "weekly_orchestrator",
                "week": week_str,
                "decision": "validator_failures",
                "attempts": validator_attempts,
                "count": len(blocking_failures),
                "failures": [item.__dict__ for item in blocking_failures],
            }
        )
        if not (stats.l5_sources and set(stats.l5_sources) == {"degraded"}):
            l5_output_dict, l6_output_dict = _sanitize_weekly_outputs(l5_output_dict, l6_output_dict, blocking_failures)

    quality_breakdown = compute_quality_score(validator_failures)
    quality_log_path = _quality_log_path()
    append_quality_log(week_str, quality_breakdown, log_path=quality_log_path)
    quality_history = _quality_history_text(week_str, quality_breakdown.total, log_path=quality_log_path)

    set_state(
        "weekly_last_outcome",
        json.dumps(
            {
                "week": week_str,
                "outcome": weekly_outcome,
                "reason": ("validator_failures" if weekly_outcome == "partial" else ""),
                "validator_failures": len(blocking_failures),
                "user_annotation": "",  # M4 抽取用户批注后填充
                "quality_breakdown": asdict(quality_breakdown),
            },
            ensure_ascii=False,
        ),
    )

    with recorder.stage("render"):
        rendered = _render_weekly_markdown(
            week_str,
            top_topics,
            {},
            l5_output_dict,
            l6_output_dict,
            cross_week_diff,
            new_principles,
            style=style,
            daily_count=len(daily_summaries),
            stats=stats,
            quality_breakdown=quality_breakdown,
            quality_history=quality_history,
            key_data_section=key_data_section,
        )
        weekly_path = _weekly_path(week_str)
    with recorder.stage("persist"):
        write_artifact(recorder, weekly_path, rendered, stage="persist_weekly_markdown")
        recorder.set_cost({"in_tokens": 0, "out_tokens": 0, "cost_usd": stats.cost_usd})
    recorder.set_output_quality("degraded" if weekly_outcome == "partial" else "ok", degraded_reason="quality_gate_refused" if weekly_outcome == "partial" else "")

    _append_log(
        {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "capability": "weekly_orchestrator",
            "week": week_str,
            "daily_count": len(daily_summaries),
            "top_topics": [str(topic.get("slug") or "") for topic in top_topics],
            "weekly_path": str(weekly_path),
        }
    )
    anchor_graph_updates = _postprocess_anchor_graph(week_str, gateway=gateway, stats=stats)
    if anchor_graph_updates["derived_updates"] or anchor_graph_updates["wikilink_updates"]:
        _append_log(
            {
                "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "capability": "weekly_orchestrator",
                "week": week_str,
                "decision": "anchor_graph_updated",
                "derived_updates": int(anchor_graph_updates["derived_updates"]),
                "wikilink_updates": int(anchor_graph_updates["wikilink_updates"]),
            }
        )

    return str(weekly_path)
