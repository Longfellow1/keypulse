from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TYPE_CHECKING

from keypulse.obsidian.layout import iso_date, render_note, slugify, time_token
from keypulse.obsidian.model import NoteCard
from keypulse.quality import StrategyRegistry, StrategyRunner
from keypulse.quality.strategies import register_cluster_strategies
from keypulse.pipeline.daily_summary import render_daily_markdown
from keypulse.pipeline.fragments import filter_noisy_raw_events
from keypulse.store.repository import query_raw_events
from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.dates import local_day_bounds

if TYPE_CHECKING:
    from keypulse.pipeline.model import ModelGateway

logger = logging.getLogger(__name__)

_cluster_registry = StrategyRegistry()
register_cluster_strategies(_cluster_registry)
_cluster_runner = StrategyRunner(_cluster_registry, log_path=None)

_TOMORROW_PLAN_HEADER = "## 明天的锚点"
_TOMORROW_PLAN_PLACEHOLDER = "______"
_TOMORROW_PLAN_LINE_PREFIX = "> 明天我想："
_TOMORROW_PLAN_HINT = "> _写一句话留给明天的自己_"
_USER_SOURCES_FOR_ITEM = frozenset({"clipboard", "manual", "browser", "browser_url"})
_SYNC_CURSOR_FILENAME = "sync-cursor.json"
_WIKI_LINK_RE = re.compile(r"\[\[(?P<target>[^\]|]+)(?:\|[^\]]+)?\]\]")
_EVENT_HASH_SUFFIX_RE = re.compile(r"-[0-9a-f]{8}$")
_SUPPORTED_WIKI_LINK_MODES = frozenset({"relative", "absolute_md"})
_FILENAME_ACTION_MAP = {
    "make": "build",
    "build": "build",
    "created": "build",
    "create": "build",
    "fix": "fix",
    "fixed": "fix",
    "修复": "fix",
    "改": "fix",
    "写": "write",
    "写了": "write",
    "test": "test",
    "testing": "test",
    "run": "run",
    "deploy": "deploy",
    "actual-success": "成功",
    "成功了": "成功",
    "实际成功了": "成功",
    "未装": "跳过",
    "没装": "跳过",
    "skip": "跳过",
}
_FILENAME_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "在",
    "的",
    "了",
    "是",
    "然后",
    "之后",
    "today",
    "yesterday",
}


@dataclass(frozen=True)
class ExportWorkBlock:
    theme: str
    duration_sec: int
    ts_start: str
    ts_end: str
    primary_app: str
    event_count: int
    key_candidates: list[dict[str, Any]]
    continuity: str = "new"
    user_candidates: list[dict[str, Any]] = field(default_factory=list)
    system_candidates: list[dict[str, Any]] = field(default_factory=list)
    subtopics: tuple[str, ...] = ()
    session_id: str | None = None
    fragment: bool = False


def _is_placeholder_tomorrow_plan(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).strip()
    return not normalized or bool(re.fullmatch(r"_+", normalized.replace(" ", "")))


def _read_tomorrow_plan(daily_md_path: Path) -> str:
    """读取日报里的 tomorrow plan 行，返回真实内容或空字符串。"""
    try:
        content = daily_md_path.read_text(encoding="utf-8")
    except OSError:
        return ""

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith(_TOMORROW_PLAN_LINE_PREFIX):
            continue
        value = stripped[len(_TOMORROW_PLAN_LINE_PREFIX) :]
        normalized = " ".join(value.split()).strip()
        return "" if _is_placeholder_tomorrow_plan(normalized) else normalized
    return ""


def _render_tomorrow_plan_section(existing_content: str = "") -> list[str]:
    """返回明天锚点段落的 markdown 行。"""
    inner = " ".join(str(existing_content or "").split()).strip()
    if _is_placeholder_tomorrow_plan(inner):
        inner = _TOMORROW_PLAN_PLACEHOLDER
    return [
        "",
        _TOMORROW_PLAN_HEADER,
        "",
        f"{_TOMORROW_PLAN_LINE_PREFIX}{inner}",
        "",
        _TOMORROW_PLAN_HINT,
    ]


def _render_previous_plan_acknowledgment(previous_plan: str) -> list[str]:
    """在报告 frontmatter 之后插入的回引。为空则返回 []。"""
    normalized = " ".join(str(previous_plan or "").split()).strip()
    if _is_placeholder_tomorrow_plan(normalized):
        return []
    return ["", f"> 💭 昨天你说想：{normalized}", ""]


def _keypulse_home() -> Path:
    keypulse_home = os.environ.get("KEYPULSE_HOME")
    if keypulse_home:
        return Path(keypulse_home).expanduser()
    return Path.home() / ".keypulse"


def _normalize_wiki_link_mode(value: str | None) -> str:
    mode = str(value or "relative").strip().lower()
    if mode not in _SUPPORTED_WIKI_LINK_MODES:
        return "relative"
    return mode


def _keypulse_relative_path_for_note(note_path: str) -> Path:
    relative = Path(note_path)
    if not relative.parts:
        return relative
    head = relative.parts[0].lower()
    if head == "events":
        return Path("events", *relative.parts[1:])
    if head == "topics":
        return Path("topics", *relative.parts[1:])
    return relative


def _build_link_formatter(
    *,
    wiki_link_mode: str,
    keypulse_home: Path,
) -> Callable[[str, str | None], str]:
    normalized_mode = _normalize_wiki_link_mode(wiki_link_mode)
    resolved_home = keypulse_home.expanduser().resolve()

    def _format(path: str, label: str | None) -> str:
        relative = _keypulse_relative_path_for_note(path)
        relative_posix = relative.as_posix()
        label_text = (label or "").strip()
        lower = relative_posix.lower()

        if lower.startswith("events/") or lower.startswith("topics/"):
            if normalized_mode == "absolute_md":
                absolute_path = (resolved_home / relative).resolve()
                link_text = label_text or Path(path).stem
                return f"[{link_text}]({absolute_path.as_uri()})"

            target = f"../.keypulse/{Path(relative_posix).with_suffix('').as_posix()}"
            if label_text:
                return f"[[{target}|{label_text}]]"
            return f"[[{target}]]"

        target = _link_from_path(path)
        if label_text:
            return f"[[{target}|{label_text}]]"
        return f"[[{target}]]"

    return _format


def _default_db_path() -> Path:
    return _keypulse_home() / "keypulse.db"


def _sync_cursor_path(cursor_path: str | Path | None = None) -> Path:
    if cursor_path is not None:
        return Path(cursor_path).expanduser()
    return _keypulse_home() / _SYNC_CURSOR_FILENAME


def _default_cursor_state() -> dict[str, Any]:
    return {"last_event_id": 0, "last_run_at": None}


def _normalize_cursor_state(payload: Any) -> dict[str, Any]:
    state = _default_cursor_state()
    if not isinstance(payload, dict):
        return state

    raw_last_id = payload.get("last_event_id", 0)
    try:
        state["last_event_id"] = max(0, int(raw_last_id))
    except (TypeError, ValueError):
        state["last_event_id"] = 0

    raw_last_run_at = payload.get("last_run_at")
    state["last_run_at"] = str(raw_last_run_at) if raw_last_run_at is not None else None
    return state


def _write_cursor_state_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _read_cursor_state(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        state = _default_cursor_state()
        _write_cursor_state_atomic(path, state)
        return state

    try:
        payload = json.loads(raw)
    except Exception:
        state = _default_cursor_state()
        _write_cursor_state_atomic(path, state)
        return state

    return _normalize_cursor_state(payload)


def _query_events_by_date(db_path: Path, date_str: str, *, min_id_exclusive: int | None = None) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        start_utc, end_utc = local_day_bounds(date_str)
        if min_id_exclusive is None:
            rows = conn.execute(
                "SELECT * FROM raw_events WHERE created_at >= ? AND created_at <= ? ORDER BY id ASC",
                (start_utc, end_utc),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM raw_events WHERE id > ? AND created_at >= ? AND created_at <= ? ORDER BY id ASC",
                (min_id_exclusive, start_utc, end_utc),
            ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


def _is_section_heading(line: str) -> bool:
    return line.startswith("## ")


def _section_body_lines(text: str, heading: str) -> list[str] | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != heading:
            continue
        end = index + 1
        while end < len(lines) and not _is_section_heading(lines[end]):
            end += 1
        return lines[index + 1 : end]
    return None


def _replace_section_body(text: str, heading: str, body_lines: list[str]) -> str:
    lines = text.splitlines()
    updated: list[str] = []
    replaced = False
    index = 0

    while index < len(lines):
        line = lines[index]
        if not replaced and line.strip() == heading:
            updated.append(line)
            updated.extend(body_lines)
            index += 1
            while index < len(lines) and not _is_section_heading(lines[index]):
                index += 1
            replaced = True
            continue

        updated.append(line)
        index += 1

    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(heading)
        updated.extend(body_lines)

    return "\n".join(updated).rstrip() + "\n"


def _trim_trailing_blank_lines(lines: list[str]) -> list[str]:
    trimmed = list(lines)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return trimmed


def _extract_wiki_link_target(line: str) -> str | None:
    match = _WIKI_LINK_RE.search(line)
    if not match:
        return None
    return match.group("target").strip()


def _event_link_key_from_target(target: str) -> str:
    stem = Path(target).stem
    return _EVENT_HASH_SUFFIX_RE.sub("", stem)


def _topic_link_key_from_target(target: str) -> str:
    normalized = str(target or "").strip().replace("\\", "/")
    lowered = normalized.lower()
    if lowered.startswith("../.keypulse/topics/"):
        normalized = normalized[len("../.keypulse/topics/") :]
    elif lowered.startswith("topics/"):
        normalized = normalized[len("topics/") :]
    return Path(normalized).with_suffix("").as_posix()


def _section_link_keys(text: str, heading: str, *, kind: str) -> set[str]:
    lines = _section_body_lines(text, heading) or []
    keys: set[str] = set()
    for line in lines:
        target = _extract_wiki_link_target(line)
        if not target:
            continue
        if kind == "event":
            keys.add(_event_link_key_from_target(target))
        else:
            keys.add(_topic_link_key_from_target(target))
    return keys


def _append_unique_section_lines(
    text: str,
    heading: str,
    new_lines: list[str],
    *,
    kind: str,
) -> str:
    existing_body = _trim_trailing_blank_lines(_section_body_lines(text, heading) or [])
    existing_keys = _section_link_keys(text, heading, kind=kind)
    appended: list[str] = []

    for line in new_lines:
        target = _extract_wiki_link_target(line)
        key = None
        if target:
            key = _event_link_key_from_target(target) if kind == "event" else _topic_link_key_from_target(target)
        else:
            key = line.strip()
        if key and key in existing_keys:
            continue
        if key:
            existing_keys.add(key)
        appended.append(line)

    if not appended:
        return text

    merged = list(existing_body)
    if merged and merged[-1].strip():
        merged.append("")
    merged.extend(appended)
    if merged and merged[-1].strip():
        merged.append("")
    return _replace_section_body(text, heading, merged)


def _replace_first_matching_line(text: str, pattern: str, replacement: str) -> str:
    lines = text.splitlines()
    compiled = re.compile(pattern)
    for index, line in enumerate(lines):
        if compiled.match(line.strip()):
            lines[index] = replacement
            return "\n".join(lines).rstrip() + "\n"
    return text


def _frontmatter_value(text: str, key: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if stripped.startswith(f"{key}:"):
            return stripped.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _extract_block(text: str, start_marker: str, end_marker: str) -> str | None:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == start_marker:
            start = index
            break
    if start is None:
        return None

    end = None
    for index in range(start + 1, len(lines)):
        if lines[index].strip() == end_marker:
            end = index
            break
    if end is None:
        return None

    return "\n".join(lines[start : end + 1])


def _replace_block(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    lines = text.splitlines()
    updated: list[str] = []
    index = 0
    replaced = False

    while index < len(lines):
        if not replaced and lines[index].strip() == start_marker:
            updated.extend(replacement.splitlines())
            index += 1
            while index < len(lines):
                if lines[index].strip() == end_marker:
                    index += 1
                    break
                index += 1
            replaced = True
            continue
        updated.append(lines[index])
        index += 1

    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.extend(replacement.splitlines())

    return "\n".join(updated).rstrip() + "\n"


def _strip_narrative_heading(text: str) -> str:
    lines = text.splitlines()
    cleaned: list[str] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if idx == 0 and stripped.startswith("#") and ("今日主线" in stripped or "今日做的事" in stripped):
            continue
        if not cleaned and not stripped:
            continue
        cleaned.append(line)
    return "\n".join(cleaned).rstrip()


def _render_dashboard_narrative(
    work_blocks: list[Any] | None,
    *,
    model_gateway: "ModelGateway | None",
) -> str:
    blocks = list(work_blocks or [])
    if model_gateway is not None and blocks:
        backend = model_gateway.select_backend("write") if hasattr(model_gateway, "select_backend") else None
        kind = getattr(backend, "kind", "") if backend is not None else ""
        url = getattr(backend, "base_url", "") if backend is not None else ""
        model = getattr(backend, "model", "") if backend is not None else ""
        available = backend is not None and kind and kind != "disabled" and model and url
        if available and hasattr(model_gateway, "render_daily_narrative"):
            try:
                result = model_gateway.render_daily_narrative(blocks).strip()
                if result:
                    return _strip_narrative_heading(result) or _render_dashboard_blocks(blocks)
            except Exception as exc:
                logger.error(
                    "dashboard_narrative fallback backend_kind=%s url=%s model=%s exc_type=%s exc=%s",
                    kind, url, model, type(exc).__name__, exc,
                )
    return _render_dashboard_blocks(blocks)


def _render_timeline_narrative(hourly_summaries: list[dict[str, Any]], db_path: Path | None, date_str: str) -> str:
    invalid_ts = datetime.max.replace(tzinfo=timezone.utc)

    def _parse_ts(value: str | None) -> datetime:
        if not value:
            return invalid_ts
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return invalid_ts

    def _event_app(item: dict[str, Any]) -> str:
        for key in ("app_name", "window_title", "process_name"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
        return "未知应用"

    def _event_title(item: dict[str, Any]) -> str:
        title = str(item.get("title") or item.get("window_title") or "").strip()
        if title:
            return " ".join(title.split())
        body = str(item.get("content_text") or item.get("body") or "").strip()
        if body:
            return " ".join(body.split())[:40]
        fallback = str(item.get("event_type") or "").strip()
        return fallback or "一些操作"

    def _unique_texts(values: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for value in values:
            text = " ".join(str(value).split()).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            unique.append(text)
        return unique

    records: list[dict[str, Any]] = []
    db_path_obj = Path(db_path).expanduser() if db_path is not None else None
    if db_path_obj is not None and db_path_obj.exists():
        try:
            records = _query_events_by_date(db_path_obj, date_str)
        except Exception as exc:
            logger.warning("timeline narrative: db query failed exc_type=%s exc=%s", type(exc).__name__, exc)
            records = []
    elif db_path_obj is None:
        try:
            since, until = local_day_bounds(date_str)
            records = query_raw_events(since=since, until=until, limit=5000)
        except Exception as exc:
            logger.warning("timeline narrative: query_raw_events failed exc_type=%s exc=%s", type(exc).__name__, exc)
            records = []

    minute_groups: list[dict[str, Any]] = []
    for item in sorted(
        records,
        key=lambda row: (
            _parse_ts(str(row.get("ts_start") or row.get("created_at") or "")),
            int(row.get("id") or 0) if str(row.get("id") or "").isdigit() else 0,
        ),
    ):
        ts = _parse_ts(str(item.get("ts_start") or item.get("created_at") or ""))
        if ts == invalid_ts:
            continue
        minute_dt = ts.replace(second=0, microsecond=0)
        app = _event_app(item)
        title = _event_title(item)
        if minute_groups and minute_groups[-1]["minute_dt"] == minute_dt:
            minute_groups[-1]["app_map"][app].append(title)
            if app not in minute_groups[-1]["app_order"]:
                minute_groups[-1]["app_order"].append(app)
            continue
        minute_groups.append(
            {
                "minute_dt": minute_dt,
                "app_order": [app],
                "app_map": defaultdict(list, {app: [title]}),
            }
        )

    beats: list[dict[str, Any]] = []
    for group in minute_groups:
        minute_dt = group["minute_dt"]
        minute_label = minute_dt.strftime("%H:%M")
        app_phrases: list[str] = []
        primary_app = ""
        primary_count = -1

        for app in group["app_order"]:
            titles = _unique_texts(list(group["app_map"].get(app) or []))
            if not titles:
                titles = ["一些操作"]
            if len(titles) > primary_count:
                primary_count = len(titles)
                primary_app = app
            app_phrases.append(f"在 {app} 做 {'、'.join(titles)}")

        if not app_phrases:
            continue

        sentence = f"{minute_label} {app_phrases[0]}"
        if len(app_phrases) > 1:
            sentence = f"{sentence}；" + "；".join(app_phrases[1:])
        beats.append(
            {
                "minute_dt": minute_dt,
                "primary_app": primary_app or group["app_order"][0],
                "sentence": sentence,
            }
        )

    if not beats:
        fallback_lines: list[str] = []
        for summary in sorted(hourly_summaries, key=lambda item: int(item.get("hour") or 0) if str(item.get("hour") or "").isdigit() else 0):
            payload = summary.get("payload") or {}
            text = str(payload.get("summary_zh") or payload.get("summary") or "").strip()
            if not text:
                continue
            try:
                hour = int(summary.get("hour") or 0)
            except (TypeError, ValueError):
                hour = 0
            fallback_lines.append(f"{hour:02d}:00 {text}")
        if fallback_lines:
            return "。".join(fallback_lines) + "。"
        return "今日没有可回放的原始事件。"

    paragraphs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for beat in beats:
        if not current:
            current.append(beat)
            continue
        previous = current[-1]
        gap_minutes = max(int((beat["minute_dt"] - previous["minute_dt"]).total_seconds() // 60), 0)
        if beat["primary_app"] == previous["primary_app"] and gap_minutes <= 20:
            current.append(beat)
            continue
        paragraphs.append(current)
        current = [beat]

    if current:
        paragraphs.append(current)

    rendered_paragraphs: list[str] = []
    for paragraph in paragraphs:
        sentence_parts: list[str] = []
        for index, beat in enumerate(paragraph):
            if index == 0:
                sentence_parts.append(beat["sentence"])
                continue
            previous = paragraph[index - 1]
            gap_minutes = max(int((beat["minute_dt"] - previous["minute_dt"]).total_seconds() // 60), 0)
            connector = "接着" if gap_minutes <= 10 else "随后" if gap_minutes <= 20 else "后来"
            sentence_parts.append(f"{connector} {beat['sentence']}")
        rendered_paragraphs.append("。".join(sentence_parts) + "。")

    return "\n\n".join(rendered_paragraphs)


def _render_db_only_narrative(db_path: Path, date_str: str) -> str:
    """Fallback: render pure data narrative from raw events without LLM."""
    timeline = _render_timeline_narrative([], db_path, date_str)
    if not timeline:
        return ""
    return "\n".join(
        [
            "> ⚠️ 今日叙事 LLM 不可用，以下为按时间线列出的原始事件（无智能总结）",
            "",
            timeline,
        ]
    ).strip()


def _source_label(source: str | None) -> str:
    return {
        "manual": "手动保存",
        "clipboard": "剪贴板",
        "window": "窗口活动",
        "ax_text": "当前看到的正文",
        "ocr_text": "屏幕识别补充",
        "keypulse": "KeyPulse",
    }.get(source or "", source or "未知来源")


def _reason_label(reason: str | None) -> str:
    return {
        "idle_event": "空闲事件",
        "low_signal_window": "低信号窗口",
        "low_density_fragment": "低密度碎片",
        "empty_content": "空内容",
        "explicitness": "明确表达",
        "novelty": "新信息",
        "reusability": "可复用",
        "decision_signal": "有判断或决策信号",
        "density": "信息密度高",
        "recurrence": "重复出现，值得关注",
    }.get(reason or "", reason or "未分类")


def _topic_title(topic_key: str | None) -> str:
    if topic_key is None:
        return "未归类"
    normalized = str(topic_key).strip()
    if normalized in {"", "topic", "uncategorized"}:
        return "未归类"
    parts = [part.strip() for part in normalized.split("-") if part.strip()]
    if not parts:
        return "未归类"
    return " / ".join(parts)


def _weekday_label(date_str: str) -> str:
    labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    try:
        return labels[datetime.fromisoformat(date_str).weekday()]
    except Exception:
        return "今天"


def _format_duration(secs: int) -> str:
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h{mins % 60:02d}m"


def _is_meaningful_topic(value: str) -> bool:
    text = str(value or "").strip()
    verdict = _cluster_runner.check(text, layer="cluster")
    return verdict.accept


def _topic_from_item(item: dict[str, Any]) -> str | None:
    candidates: list[str] = []

    tags = item.get("tags")
    if tags:
        parts = [part.strip() for part in str(tags).split(",") if part.strip()]
        if parts:
            candidates.append("-".join(parts))

    for field in ("title", "window_title", "body"):
        value = item.get(field)
        if value:
            candidates.append(str(value))

    for candidate in candidates:
        if _is_meaningful_topic(candidate):
            return slugify(candidate, fallback="topic", max_length=64)

    return None


def _event_slug(item: dict[str, Any], topic_key: str) -> str:
    title = item.get("title") or item.get("window_title") or item.get("app_name") or topic_key
    return slugify(str(title), fallback=topic_key, max_length=64)


def _hash_suffix(*parts: str) -> str:
    digest = hashlib.sha1("::".join(parts).encode("utf-8")).hexdigest()
    return digest[:8]


def _event_note_id(item: dict[str, Any], date_str: str, topic_key: str) -> str:
    title = str(item.get("title") or item.get("window_title") or item.get("app_name") or topic_key or "").strip()
    created_at = str(item.get("created_at") or "")
    return _hash_suffix(date_str, topic_key, created_at, title)


def _normalize_filename_token(token: str) -> str:
    normalized = slugify(token, fallback="", max_length=32)
    if not normalized:
        return ""
    return _FILENAME_ACTION_MAP.get(normalized, normalized)


def _filename_tokens_from_text(text: str) -> list[str]:
    slug = slugify(text, fallback="", max_length=120)
    if not slug:
        return []
    out: list[str] = []
    for raw in slug.split("-"):
        token = _normalize_filename_token(raw)
        if not token:
            continue
        out.append(token)
    return out


def _fallback_intent(item: dict[str, Any]) -> str:
    event_type = str(item.get("event_type") or "").strip().lower()
    if event_type == "manual_save":
        return "capture"
    if event_type:
        return _normalize_filename_token(event_type) or "capture"
    return "capture"


def _build_event_filename_slug(item: dict[str, Any], title_text: str, topic_key: str) -> str:
    tokens = _filename_tokens_from_text(title_text)
    if not tokens or (tokens[0].isascii() and len(tokens[0]) < 2):
        tokens = _filename_tokens_from_text(str(item.get("app_name") or item.get("origin_source") or topic_key or "event"))
    actor = tokens[0] if tokens else "event"
    intent = tokens[1] if len(tokens) >= 2 else _fallback_intent(item)
    keyword_sources = tokens[2:] + _filename_tokens_from_text(str(topic_key))
    keywords: list[str] = []
    for token in keyword_sources:
        if token in {actor, intent}:
            continue
        if token in _FILENAME_STOPWORDS:
            continue
        if token.isascii() and len(token) < 2:
            continue
        if token not in keywords:
            keywords.append(token)
        if len(keywords) >= 4:
            break
    parts = [actor, intent, *keywords]
    deduped: list[str] = []
    for token in parts:
        if token and token not in deduped:
            deduped.append(token)
    slug = "-".join(deduped)
    return slugify(slug, fallback="event-capture", max_length=96)


def _humanize_event_title(
    item: dict[str, Any],
    topic_key: str,
    *,
    model_gateway: "ModelGateway | None",
    humanize_titles: bool,
) -> str:
    original_title = str(item.get("title") or "").strip()
    fallback_title = original_title or str(item.get("app_name") or topic_key or "event").strip()
    if not humanize_titles or model_gateway is None:
        return fallback_title

    prompt_input = {
        "title": fallback_title,
        "body": str(item.get("body") or "").strip(),
        "app_name": str(item.get("app_name") or "").strip(),
        "topic_key": str(topic_key or "").strip(),
    }
    prompt = "\n".join(
        [
            "请把下面事件标题重写成“actor + intent + 关键词”短标题，避免机器味。",
            "输出 JSON，字段 filename_title。",
            json.dumps(prompt_input, ensure_ascii=False),
        ]
    )
    try:
        response = model_gateway.call(
            "event_title_humanize",
            prompt,
            input_data=prompt_input,
        )
    except Exception as exc:
        logger.warning("event title humanize fallback exc_type=%s exc=%s", type(exc).__name__, exc)
        return fallback_title
    if not isinstance(response, dict):
        return fallback_title
    candidate = " ".join(str(response.get("filename_title") or "").split()).strip()
    if not candidate:
        return fallback_title
    return candidate


def _event_filename(
    item: dict[str, Any],
    date_str: str,
    topic_key: str,
    *,
    model_gateway: "ModelGateway | None" = None,
    humanize_titles: bool = False,
) -> str:
    created_at = str(item.get("created_at") or "")
    title = _humanize_event_title(
        item,
        topic_key,
        model_gateway=model_gateway,
        humanize_titles=humanize_titles,
    )
    event_slug = _build_event_filename_slug(item, title, topic_key)
    return f"{time_token(created_at)}-{event_slug}.md"


def _meaningful_item(event: dict[str, Any]) -> bool:
    speaker = event.get("speaker") or ("user" if event.get("source") in _USER_SOURCES_FOR_ITEM else "system")
    if speaker != "user":
        return False
    source = event.get("source")
    content = (event.get("content_text") or event.get("body") or "").strip()
    title = (event.get("window_title") or event.get("title") or "").strip()
    if source in _USER_SOURCES_FOR_ITEM:
        return bool(content or title)
    return False


def _is_loginwindow_render_item(item: dict[str, Any]) -> bool:
    app_name = str(item.get("app_name") or "").strip().lower()
    if app_name == "loginwindow":
        return True
    title = str(item.get("title") or item.get("window_title") or "").strip().lower()
    return title == "loginwindow"


def _to_item(event: dict[str, Any]) -> dict[str, Any] | None:
    if not _meaningful_item(event):
        return None

    body = (event.get("content_text") or event.get("body") or event.get("window_title") or event.get("app_name") or "").strip()
    title = _title_from_event(event, body)
    topic_key = _topic_from_item({
        "tags": _extract_tags(event),
        "title": title,
        "window_title": event.get("window_title"),
        "app_name": event.get("app_name"),
        "body": body,
    })
    if topic_key is None:
        topic_key = "uncategorized"

    return {
        "created_at": event.get("ts_start") or event.get("created_at"),
        "source": "keypulse",
        "origin_source": event.get("source"),
        "speaker": event.get("speaker") or ("user" if event.get("source") in _USER_SOURCES_FOR_ITEM else "system"),
        "event_type": event.get("event_type"),
        "session_id": event.get("session_id"),
        "title": title,
        "body": body,
        "app_name": event.get("app_name"),
        "window_title": event.get("window_title"),
        "tags": _extract_tags(event),
        "topic_key": topic_key,
        "confidence": _confidence(event),
    }


def _extract_tags(event: dict[str, Any]) -> str | None:
    metadata_json = event.get("metadata_json")
    if not metadata_json:
        return None
    try:
        metadata = json.loads(metadata_json)
    except Exception:
        return None
    tags = metadata.get("tags")
    if not tags:
        return None
    return str(tags)


def _confidence(event: dict[str, Any]) -> float:
    source = event.get("source")
    if source == "manual":
        return 1.0
    if source == "clipboard":
        return 0.85
    return 0.5


def _parse_item_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.max.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _item_duration(events: list[dict[str, Any]]) -> int:
    if len(events) < 2:
        return 0
    start = min((_parse_item_datetime(str(item.get("created_at") or "")) for item in events), default=datetime.max.replace(tzinfo=timezone.utc))
    end = max((_parse_item_datetime(str(item.get("created_at") or "")) for item in events), default=datetime.max.replace(tzinfo=timezone.utc))
    if start == datetime.max.replace(tzinfo=timezone.utc) or end <= start:
        return 0
    return int((end - start).total_seconds())


def _aggregate_export_work_blocks(
    items: list[dict[str, Any]] | None,
    *,
    sessions: list[dict[str, Any]] | None = None,
    recent_topic_keys: set[str] | None = None,
    previous_day_topic_keys: set[str] | None = None,
) -> list[ExportWorkBlock]:
    session_by_id = {str(session.get("id") or ""): session for session in (sessions or []) if str(session.get("id") or "").strip()}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, item in enumerate(items or []):
        topic_key = str(item.get("topic_key") or "uncategorized").strip() or "uncategorized"
        session_id = str(item.get("session_id") or "").strip()
        grouped[session_id or f"topic:{topic_key}:{index if topic_key == 'uncategorized' else ''}"].append(item)

    blocks: list[ExportWorkBlock] = []
    for group_items in grouped.values():
        ordered = sorted(group_items, key=lambda item: _parse_item_datetime(str(item.get("created_at") or "")))
        if not ordered:
            continue
        topic_counts = Counter(str(item.get("topic_key") or "uncategorized") for item in ordered)
        theme = topic_counts.most_common(1)[0][0]
        app_counts = Counter(str(item.get("app_name") or item.get("window_title") or "").strip() for item in ordered)
        app_counts.pop("", None)
        ts_start = str(ordered[0].get("created_at") or "")
        ts_end = str(ordered[-1].get("created_at") or ts_start)
        duration = _item_duration(ordered)
        session_id = str(ordered[0].get("session_id") or "").strip()
        if duration <= 0 and session_id in session_by_id:
            try:
                duration = max(int(session_by_id[session_id].get("duration_sec") or 0), 0)
            except Exception:
                duration = 0
        user_candidates = [item for item in ordered if str(item.get("speaker") or "") == "user"][:3]
        system_candidates = [item for item in ordered if str(item.get("speaker") or "") != "user"][:3]
        blocks.append(
            ExportWorkBlock(
                theme=theme,
                duration_sec=duration,
                ts_start=ts_start,
                ts_end=ts_end,
                primary_app=app_counts.most_common(1)[0][0] if app_counts else "",
                event_count=len(ordered),
                key_candidates=ordered[:3],
                continuity=(
                    "continued"
                    if previous_day_topic_keys and theme in previous_day_topic_keys
                    else "returned"
                    if recent_topic_keys and theme in recent_topic_keys
                    else "new"
                ),
                user_candidates=user_candidates,
                system_candidates=system_candidates,
                subtopics=tuple(dict.fromkeys(str(item.get("topic_key") or "uncategorized") for item in ordered)),
                session_id=session_id or None,
                fragment=duration < 300 or not user_candidates,
            )
        )
    return sorted(blocks, key=lambda block: _parse_item_datetime(block.ts_start))


def _summary_events_from_topics(topics: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    summary_events: list[dict[str, Any]] = []
    for topic_key, topic_items in sorted(topics.items()):
        ordered = sorted(topic_items, key=lambda item: str(item.get("created_at") or ""))
        if not ordered:
            continue
        summary_events.append(
            {
                "cluster_id": topic_key,
                "display_name": _topic_title(topic_key),
                "narrative_one_line": "；".join(str(item.get("title") or "").strip() for item in ordered[:3] if str(item.get("title") or "").strip()),
                "event_count": len(ordered),
                "time_range": ["00:00", "23:59"],
                "anchored_to": topic_key if topic_key != "uncategorized" else None,
                "merge_candidate_with": [],
            }
        )
    return summary_events


def _summary_topics_from_blocks(blocks: list[ExportWorkBlock], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    event_by_id = {str(event.get("cluster_id") or ""): event for event in events}
    topics: list[dict[str, Any]] = []
    for block in blocks:
        if block.fragment or not block.theme or block.theme == "uncategorized":
            continue
        event_payload = event_by_id.get(block.theme) or {}
        narrative = str(event_payload.get("narrative_one_line") or "").strip()
        if len(narrative) < 40:
            narrative = f"{_topic_title(block.theme)} 在当天形成了 {block.event_count} 条事件记录，主要围绕 {block.primary_app or '当前工作'} 展开。"
        topics.append(
            {
                "anchor": block.theme,
                "anchor_state": "continuing",
                "narrative": narrative,
                "decisions": [],
                "shipped": [narrative] if narrative else [],
                "events_ref": [block.theme],
                "display": _topic_title(block.theme),
            }
        )
    return topics


def _short_body_title(text: str, fallback: str = "event") -> str:
    for raw_line in str(text).splitlines():
        line = " ".join(raw_line.strip().lstrip("#*-").split())
        if line:
            return line[:72]
    return fallback


def _title_from_event(event: dict[str, Any], body: str) -> str:
    explicit_title = (event.get("title") or event.get("window_title") or "").strip()
    if explicit_title:
        return explicit_title
    if event.get("source") in {"manual", "clipboard"} and body:
        return _short_body_title(body, fallback="event")
    return (event.get("app_name") or event.get("event_type") or "event").strip()


def _note_path(kind: str, date_str: str, topic_key: str, created_at: str | None = None) -> str:
    if kind == "daily":
        return f"Daily/{date_str}.md"
    if kind == "topic":
        return f"Topics/{topic_key}.md"
    token = time_token(created_at)
    suffix = _hash_suffix(date_str, topic_key, created_at or "")
    return f"Events/{date_str}/{token}-{topic_key}-{suffix}.md"


def _link_from_path(path: str) -> str:
    return path.removesuffix(".md")


def _obsidian_link(
    path: str,
    label: str | None = None,
    *,
    link_formatter: Callable[[str, str | None], str] | None = None,
) -> str:
    if link_formatter is not None:
        return link_formatter(path, label)
    target = _link_from_path(path)
    if label and label.strip():
        return f"[[{target}|{label.strip()}]]"
    return f"[[{target}]]"


def _event_identity(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("created_at") or item.get("ts_start") or ""),
        str(item.get("title") or ""),
        str(item.get("topic_key") or ""),
    )


def _existing_topic_alias(topics_dir: Path, item: dict[str, Any]) -> str | None:
    tags = str(item.get("tags") or "").strip()
    if not tags:
        return None
    parts = [part.strip() for part in tags.split(",") if part.strip()]
    if len(parts) < 2:
        return None
    candidate = slugify("-".join(parts), fallback="topic")
    topic_path = topics_dir / f"{candidate}.md"
    return candidate if topic_path.exists() else None


def _preview_links(links: list[str], label: str, limit: int = 8) -> list[str]:
    preview = [f"- {link}" for link in links[:limit]]
    if len(links) > limit:
        preview.append(f"- 另有 {len(links) - limit} 条{label}已生成单独笔记")
    return preview


def _render_dashboard_blocks(work_blocks: list[Any] | None, limit: int = 5) -> str:
    blocks = sorted((block for block in work_blocks or [] if not getattr(block, "fragment", False)), key=lambda block: (-getattr(block, "duration_sec", 0), getattr(block, "ts_start", "")))[:limit]
    if not blocks:
        return "今天没有形成足够清晰的工作块。"
    lines: list[str] = []
    for block in blocks:
        subtopics = [subtopic for subtopic in getattr(block, "subtopics", ()) if subtopic and subtopic != block.theme][:3]
        primary_app = getattr(block, 'primary_app', '') or '未知应用'
        # Avoid self-reference when theme == primary_app
        if block.theme == primary_app:
            summary = f"你在 {primary_app} 里专注工作了"
        else:
            summary = f"你在 {primary_app} 里推进了 {block.theme}"
        if subtopics:
            summary += f"，涉及 {len(subtopics)} 个子主题"
        lines.extend([f"### {block.theme} · {_format_duration(getattr(block, 'duration_sec', 0))}", "", f"> {summary}。", ""])
    return "\n".join(lines).strip()


def _render_dashboard_body(
    snapshot: dict[str, Any],
    date_str: str,
    *,
    work_blocks: list[Any] | None = None,
    evidence_paths: dict[tuple[str, str, str], str] | None = None,
    model_gateway: "ModelGateway | None" = None,
    previous_plan: str = "",
) -> str:
    candidates = snapshot.get("candidates", [])
    filtered_reasons = snapshot.get("filtered_reasons", {})
    theme_candidates = snapshot.get("theme_candidates", [])
    top_block = max((block for block in work_blocks or [] if not getattr(block, "fragment", False)), default=None, key=lambda block: getattr(block, "duration_sec", 0))
    top_theme = getattr(top_block, "theme", "") or _topic_title(str(theme_candidates[0]["topic_key"])) if theme_candidates else "今天"
    lead_lines = [
        f"> 主战场是 {top_theme}。",
    ]
    top_signals = [
        "\n".join(
            [
                f"- {item['title']}",
                f"  - 价值分：{item['score']}",
                f"  - 来源：{_source_label(item.get('origin_source') or item.get('source'))}",
                f"  - 为什么保留：{', '.join(item.get('why_labels') or [])}",
            ]
        )
        for item in candidates
    ] or ["- 今天还没有值得重点处理的候选内容"]
    filtered = [
        f"- {_reason_label(reason)}：{count}"
        for reason, count in sorted(filtered_reasons.items())
    ] or ["- 今天没有过滤掉明显噪音"]
    themes = [
        "\n".join(
            [
                f"- {_topic_title(item['topic_key'])}",
                f"  - 当前证据数：{item['item_count']}",
                f"  - 平均价值分：{item['avg_score']}",
                f"  - 代表证据：{item['top_evidence']}",
            ]
        )
        for item in theme_candidates
    ] or ["- 还没有形成稳定的主题候选"]
    review_queue = ["- 当前没有需要你手动确认的内容"]

    return "\n".join(
        [
            f"# {date_str} · {_weekday_label(date_str)}",
            "",
            *_render_previous_plan_acknowledgment(previous_plan),
            *lead_lines,
            "",
            "## 🎯 今日主线",
            _render_dashboard_narrative(work_blocks, model_gateway=model_gateway),
            "",
            "## 今天最值得看的内容",
            *top_signals,
            "",
            "## 已自动过滤的内容",
            *filtered,
            "",
            "## 正在形成的主题",
            *themes,
            "",
            "## 📌 附录",
            "<details>",
            f"<summary>完整事件 {len(candidates)} 条 · 过滤 {snapshot.get('filtered_total', 0)} 条</summary>",
            "",
            *review_queue,
            "",
            "</details>",
        ]
    )


def _build_note_card(
    kind: str,
    date_str: str,
    topic_key: str,
    item: dict[str, Any],
    body: str,
    extra_props: dict[str, Any] | None = None,
    path: str | None = None,
) -> NoteCard:
    properties = {
        "type": kind,
        "source": "keypulse",
        "date": date_str,
        "topic": topic_key,
    }
    if extra_props:
        properties.update(extra_props)
    return NoteCard(
        path=path or _note_path(kind, date_str, topic_key, item.get("created_at")),
        properties=properties,
        body=body,
    )


def _build_topic_card(
    vault_name: str,
    date_str: str,
    topic_key: str,
    topic_items: list[dict[str, Any]],
    *,
    link_formatter: Callable[[str, str | None], str] | None = None,
) -> NoteCard:
    event_summaries: list[str] = []
    for item in topic_items:
        event_link = _obsidian_link(
            _note_path("event", date_str, topic_key, item.get("created_at")),
            item["title"],
            link_formatter=link_formatter,
        )
        event_summaries.append(f"- {event_link} - {item['title']}")

    topic_body = "\n".join(
        [
            f"# {_topic_title(topic_key)}",
            "",
            f"- 知识库：{vault_name}",
            f"- 关联片段：{len(topic_items)}",
            "",
            "## 相关证据",
            *event_summaries,
        ]
    )

    return _build_note_card(
        "topic",
        date_str,
        topic_key,
        topic_items[0],
        topic_body,
        extra_props={
            "item_count": len(topic_items),
            "vault": vault_name,
        },
    )


def build_obsidian_bundle(
    items: list[dict[str, Any]],
    vault_name: str,
    date_str: str,
    *,
    sessions: list[dict[str, Any]] | None = None,
    recent_topic_keys: set[str] | None = None,
    previous_day_topic_keys: set[str] | None = None,
    recent_topic_counts: dict[str, int] | None = None,
    model_gateway: "ModelGateway | None" = None,
    previous_plan: str = "",
    current_plan_existing: str = "",
    db_path: str | Path | None = None,
    wiki_link_mode: str = "relative",
    humanize_titles: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    raw_count = len(items)
    items = filter_noisy_raw_events(items)
    items = [item for item in items if not _is_loginwindow_render_item(item)]
    logger.info("hygiene_filter raw=%d kept=%d dropped=%d", raw_count, len(items), raw_count - len(items))
    normalized = [item for item in (_to_item(event) for event in items) if item is not None]
    work_blocks = _aggregate_export_work_blocks(
        normalized,
        sessions=sessions,
        recent_topic_keys=recent_topic_keys,
        previous_day_topic_keys=previous_day_topic_keys,
    )
    work_item_count = len(work_blocks)
    work_topic_count = len({block.theme for block in work_blocks if not block.fragment})
    topic_block_counts = Counter(block.theme for block in work_blocks if not block.fragment)
    link_formatter = _build_link_formatter(
        wiki_link_mode=wiki_link_mode,
        keypulse_home=_keypulse_home(),
    )

    topics: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in normalized:
        topics[item["topic_key"]].append(item)

    daily_links: list[str] = []
    daily_topic_links: list[str] = []
    event_cards: list[NoteCard] = []
    topic_cards: list[NoteCard] = []
    evidence_paths: dict[tuple[str, str, str], str] = {}

    for topic_key, topic_items in sorted(topics.items()):
        topic_items = sorted(topic_items, key=lambda item: item["created_at"] or "")

        for item in topic_items:
            event_card = _build_event_card(
                item,
                date_str,
                topic_key,
                link_formatter=link_formatter,
                model_gateway=model_gateway,
                humanize_titles=humanize_titles,
            )
            event_cards.append(event_card)
            event_link = _obsidian_link(event_card.path, item["title"], link_formatter=link_formatter)
            evidence_paths[_event_identity(item)] = event_card.path
            daily_links.append(event_link)

        if topic_key == "uncategorized" or topic_block_counts.get(topic_key, 0) < 2:
            continue

        topic_card = _build_topic_card(
            vault_name,
            date_str,
            topic_key,
            topic_items,
            link_formatter=link_formatter,
        )
        topic_cards.append(topic_card)
        daily_topic_links.append(_obsidian_link(topic_card.path, _topic_title(topic_key), link_formatter=link_formatter))

    summary_events = _summary_events_from_topics(topics)
    summary_topics = _summary_topics_from_blocks(work_blocks, summary_events)
    daily_event_cards = [
        (Path(card.path).stem, str(card.properties.get("title") or Path(card.path).stem))
        for card in event_cards
    ]
    daily_body = render_daily_markdown(
        date=date_str,
        topics=summary_topics,
        events=summary_events,
        unanchored=[event for event in summary_events if event.get("anchored_to") is None],
        topic_snapshot={
            topic["anchor"]: {
                "name": topic.get("display") or topic["anchor"],
                "state": "in_progress",
                "last_seen_date": date_str,
                "evidence_dates": [date_str],
            }
            for topic in summary_topics
        },
        model_gateway=model_gateway,
        event_cards=daily_event_cards,
        previous_plan=previous_plan,
        tomorrow_plan=current_plan_existing,
    )

    daily_card = _build_note_card(
        "daily",
        date_str,
        "daily",
        {"created_at": f"{date_str}T00:00:00+00:00"},
        daily_body,
        extra_props={
            "vault": vault_name,
            "item_count": work_item_count,
            "topic_count": work_topic_count,
        },
    )

    return {
        "daily": [daily_card.as_dict()],
        "events": [card.as_dict() for card in event_cards],
        "topics": [card.as_dict() for card in topic_cards],
    }


def _build_event_card(
    item: dict[str, Any],
    date_str: str,
    topic_key: str,
    *,
    link_formatter: Callable[[str, str | None], str] | None = None,
    model_gateway: "ModelGateway | None" = None,
    humanize_titles: bool = False,
) -> NoteCard:
    event_path = (
        f"Events/{date_str}/"
        f"{_event_filename(item, date_str, topic_key, model_gateway=model_gateway, humanize_titles=humanize_titles)}"
    )
    body = "\n".join(
        [
            f"# {item['title']}",
            "",
            f"- 来源：{_source_label(item['origin_source'])}",
            f"- 应用：{item.get('app_name') or '—'}",
            f"- 置信度：{item['confidence']}",
            f"- 所属主题：{_obsidian_link(_note_path('topic', date_str, topic_key), _topic_title(topic_key), link_formatter=link_formatter)}",
            "",
            "## 原始内容",
            item["body"] or "_当前没有可展示的正文_",
        ]
    )
    extra = {
        "origin_source": item["origin_source"],
        "event_type": item["event_type"],
        "app": item.get("app_name") or "",
        "confidence": item["confidence"],
        "topic": topic_key,
        "vault": item.get("vault", "KeyPulse"),
        "id": _event_note_id(item, date_str, topic_key),
    }
    return _build_note_card("event", date_str, topic_key, item, body, extra_props=extra, path=event_path)


def write_obsidian_bundle(bundle: dict[str, list[dict[str, Any]]], output_dir: str | Path) -> list[Path]:
    output_path = Path(output_dir).expanduser()
    keypulse_home = _keypulse_home()
    written: list[Path] = []
    event_dates = {
        Path(note["path"]).parts[1]
        for note in bundle.get("events", [])
        if len(Path(note["path"]).parts) >= 3
    }
    for date_str in event_dates:
        event_dir = keypulse_home / "events" / date_str
        if event_dir.exists():
            for stale in event_dir.glob("*.md"):
                stale.unlink()
    for section in ("daily", "events", "topics"):
        for note in bundle.get(section, []):
            relative = Path(note["path"])
            if section == "daily":
                target = output_path / relative
            else:
                target = keypulse_home / _keypulse_relative_path_for_note(relative.as_posix())
            new_text = render_note(note["properties"], note["body"])
            if section == "daily":
                from keypulse.obsidian.quality_gate import (
                    build_quality_gate_placeholder,
                    should_write_daily,
                )

                ok, reason, new_score, _old_score = should_write_daily(new_text, target)
                if not ok:
                    date_str = target.stem
                    placeholder_body = build_quality_gate_placeholder(
                        date_str=date_str,
                        score=new_score,
                        reason=reason,
                    )
                    new_text = render_note(note["properties"], placeholder_body)
                    logger.warning("daily write replaced with placeholder (%s): %s", target.name, reason)
            atomic_write_text(target, new_text)
            written.append(target)
    return written


def _write_note_if_missing(output_path: Path, keypulse_home: Path, note: dict[str, Any]) -> Path | None:
    relative = Path(note["path"])
    if relative.parts and relative.parts[0].lower() in {"events", "topics"}:
        target = keypulse_home / _keypulse_relative_path_for_note(relative.as_posix())
    else:
        target = output_path / relative
    if target.exists():
        return None
    atomic_write_text(target, render_note(note["properties"], note["body"]))
    return target


def export_obsidian_incremental(
    db_path: str | Path,
    vault_path: str | Path,
    cursor_path: str | Path | None,
    date: str,
    *,
    vault_name: str = "KeyPulse",
    wiki_link_mode: str = "relative",
    model_gateway: "ModelGateway | None" = None,
    humanize_titles: bool = False,
) -> list[Path]:
    output_path = Path(vault_path).expanduser()
    keypulse_home = _keypulse_home()
    topics_dir = keypulse_home / "topics"
    date_str = iso_date(date)
    db_path_resolved = Path(db_path).expanduser()
    cursor_file = _sync_cursor_path(cursor_path)
    cursor_state = _read_cursor_state(cursor_file)
    last_event_id = int(cursor_state.get("last_event_id") or 0)
    link_formatter = _build_link_formatter(
        wiki_link_mode=wiki_link_mode,
        keypulse_home=keypulse_home,
    )

    if db_path_resolved.exists():
        window_raw_events = _query_events_by_date(db_path_resolved, date_str, min_id_exclusive=last_event_id)
    else:
        day_since, day_until = local_day_bounds(date_str)
        raw_events = query_raw_events(since=day_since, until=day_until, limit=5000)
        if any(row.get("id") is not None for row in raw_events):
            window_raw_events = [row for row in raw_events if int(row.get("id") or 0) > last_event_id]
        else:
            window_raw_events = list(raw_events)
    written: list[Path] = []

    event_items: list[dict[str, Any]] = []
    for raw_event in sorted(window_raw_events, key=lambda row: str(row.get("ts_start") or "")):
        item = _to_item(raw_event)
        if item is not None:
            event_items.append(item)

    topic_items_by_key: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)

    for item in event_items:
        event_card = _build_event_card(
            item,
            date_str,
            item["topic_key"],
            link_formatter=link_formatter,
            model_gateway=model_gateway,
            humanize_titles=humanize_titles,
        )
        topic_bucket = _existing_topic_alias(topics_dir, item) or item["topic_key"]
        written_path = _write_note_if_missing(output_path, keypulse_home, event_card.as_dict())
        if written_path is not None:
            topic_items_by_key[topic_bucket].append((item, event_card.path))
            written.append(written_path)

    for topic_key, topic_entries in sorted(topic_items_by_key.items()):
        topic_path = topics_dir / f"{topic_key}.md"
        if topic_path.exists():
            topic_text = _read_text(topic_path)
            existing_topic_event_keys = _section_link_keys(topic_text, "## 相关证据", kind="event")
            topic_lines_to_add: list[str] = []

            for item, event_path in topic_entries:
                event_key = _event_link_key_from_target(event_path)
                if event_key in existing_topic_event_keys:
                    continue
                existing_topic_event_keys.add(event_key)
                topic_lines_to_add.append(
                    f"- {_obsidian_link(event_path, item['title'], link_formatter=link_formatter)} - {item['title']}"
                )

            if topic_lines_to_add:
                updated_text = _replace_first_matching_line(
                    topic_text,
                    r"^- 关联片段：\s*\d+\s*$",
                    f"- 关联片段：{len(existing_topic_event_keys)}",
                )
                updated_text = _append_unique_section_lines(updated_text, "## 相关证据", topic_lines_to_add, kind="event")
                _write_text(topic_path, updated_text)
                written.append(topic_path)

            continue

        if topic_key == "uncategorized" or len(topic_entries) < 2:
            continue

        topic_items = [item for item, _event_path in topic_entries]
        topic_card = _build_topic_card(
            vault_name,
            date_str,
            topic_key,
            topic_items,
            link_formatter=link_formatter,
        )
        written_path = _write_note_if_missing(output_path, keypulse_home, topic_card.as_dict())
        if written_path is not None:
            written.append(written_path)

    max_new_id = max((int(row.get("id") or 0) for row in window_raw_events), default=last_event_id)
    _write_cursor_state_atomic(
        cursor_file,
        {
            "last_event_id": max(last_event_id, max_new_id),
            "last_run_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return written


def _export_obsidian_incremental(
    output_dir: str | Path,
    vault_name: str,
    date_str: str,
    *,
    db_path: str | Path | None = None,
    cursor_path: str | Path | None = None,
    wiki_link_mode: str = "relative",
    model_gateway: "ModelGateway | None" = None,
    humanize_titles: bool = False,
) -> list[Path]:
    resolved_db_path = Path(db_path).expanduser() if db_path is not None else _default_db_path()
    return export_obsidian_incremental(
        resolved_db_path,
        output_dir,
        cursor_path,
        date_str,
        vault_name=vault_name,
        wiki_link_mode=wiki_link_mode,
        model_gateway=model_gateway,
        humanize_titles=humanize_titles,
    )


def export_obsidian(
    output_dir: str | Path,
    vault_name: str = "KeyPulse",
    days: Optional[int] = None,
    date_str: Optional[str] = None,
    model_gateway: "ModelGateway | None" = None,
    incremental: bool = False,
    db_path: str | Path | None = None,
    cursor_path: str | Path | None = None,
    wiki_link_mode: str = "relative",
    humanize_titles: bool = False,
) -> list[Path]:
    normalized_wiki_link_mode = _normalize_wiki_link_mode(wiki_link_mode)
    if date_str:
        since, until = local_day_bounds(date_str)
        effective_date = iso_date(date_str)
    elif days:
        effective_date = datetime.now(timezone.utc).date().isoformat()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        until = None
    else:
        effective_date = datetime.now().date().isoformat()
        since = None
        until = None

    if incremental:
        return _export_obsidian_incremental(
            output_dir,
            vault_name,
            effective_date,
            db_path=db_path,
            cursor_path=cursor_path,
            wiki_link_mode=normalized_wiki_link_mode,
            model_gateway=model_gateway,
            humanize_titles=humanize_titles,
        )

    output_path = Path(output_dir).expanduser()
    events = query_raw_events(since=since, until=until, limit=5000)
    sessions = []
    try:
        from keypulse.store.repository import get_sessions

        sessions = get_sessions(date_str=effective_date, limit=500)
    except Exception:
        sessions = []

    effective_dt = datetime.fromisoformat(f"{effective_date}T00:00:00+00:00")
    recent_since = (effective_dt - timedelta(days=7)).date().isoformat()
    previous_day = (effective_dt - timedelta(days=1)).date().isoformat()
    recent_events = query_raw_events(since=f"{recent_since}T00:00:00+00:00", until=f"{previous_day}T23:59:59+00:00", limit=10000)
    previous_day_events = query_raw_events(since=f"{previous_day}T00:00:00+00:00", until=f"{previous_day}T23:59:59+00:00", limit=10000)
    recent_blocks = _aggregate_export_work_blocks([item for item in (_to_item(event) for event in recent_events) if item is not None])
    recent_topic_keys = {block.theme for block in recent_blocks if not block.fragment}
    recent_topic_counts = Counter(block.theme for block in recent_blocks if not block.fragment)
    previous_blocks = _aggregate_export_work_blocks([item for item in (_to_item(event) for event in previous_day_events) if item is not None])
    previous_day_topic_keys = {block.theme for block in previous_blocks if not block.fragment}
    previous_plan = _read_tomorrow_plan(output_path / "Daily" / f"{previous_day}.md")
    current_plan_existing = _read_tomorrow_plan(output_path / "Daily" / f"{effective_date}.md")

    bundle = build_obsidian_bundle(
        events,
        vault_name=vault_name,
        date_str=effective_date,
        sessions=sessions,
        recent_topic_keys=recent_topic_keys,
        previous_day_topic_keys=previous_day_topic_keys,
        recent_topic_counts=dict(recent_topic_counts),
        model_gateway=model_gateway,
        previous_plan=previous_plan,
        current_plan_existing=current_plan_existing,
        db_path=db_path,
        wiki_link_mode=normalized_wiki_link_mode,
        humanize_titles=humanize_titles,
    )
    written = write_obsidian_bundle(bundle, output_dir)
    return written
