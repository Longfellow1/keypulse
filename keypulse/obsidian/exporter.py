from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, TYPE_CHECKING

from keypulse.obsidian.layout import iso_date, render_note, slugify, time_token
from keypulse.obsidian.model import NoteCard
from keypulse.quality import StrategyRegistry, StrategyRunner
from keypulse.quality.strategies import register_cluster_strategies
from keypulse.pipeline.decisions import build_daily_decisions, render_daily_decisions
from keypulse.pipeline.narrative import aggregate_work_blocks, render_daily_narrative
from keypulse.pipeline.surface import build_surface_snapshot
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
_USER_SOURCES_FOR_ITEM = frozenset({"keyboard_chunk", "clipboard", "manual", "browser"})
_SYNC_CURSOR_FILENAME = "sync-cursor.json"
_WIKI_LINK_RE = re.compile(r"\[\[(?P<target>[^\]|]+)(?:\|[^\]]+)?\]\]")
_EVENT_HASH_SUFFIX_RE = re.compile(r"-[0-9a-f]{8}$")


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
        if min_id_exclusive is None:
            rows = conn.execute(
                "SELECT * FROM raw_events WHERE date(created_at) = ? ORDER BY id ASC",
                (date_str,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM raw_events WHERE id > ? AND date(created_at) = ? ORDER BY id ASC",
                (min_id_exclusive, date_str),
            ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _query_sessions_by_date(db_path: Path, date_str: str, *, limit: int = 500) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE started_at LIKE ? ORDER BY started_at ASC LIMIT ?",
            (f"{date_str}%", limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _max_ts_start(rows: list[dict[str, Any]]) -> str | None:
    timestamps = [str(row.get("ts_start") or "").strip() for row in rows if str(row.get("ts_start") or "").strip()]
    return max(timestamps) if timestamps else None


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
    return Path(target).with_suffix("").as_posix()


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


def _replace_or_append_top_line(text: str, prefix: str, value: int) -> str:
    lines = text.splitlines()
    pattern = re.compile(rf"^{re.escape(prefix)}\s*\d+\s*$")
    replaced = False
    for index, line in enumerate(lines):
        if pattern.match(line.strip()):
            lines[index] = f"{prefix} {value}"
            replaced = True
            break
    if not replaced:
        return text
    return "\n".join(lines).rstrip() + "\n"


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
        if idx == 0 and stripped.startswith("##") and "今日主线" in stripped:
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


def _render_daily_narrative_v2_or_legacy(
    work_blocks: list[Any],
    *,
    model_gateway: "ModelGateway | None",
    evidence_formatter: Callable[[dict[str, Any]], str] | None,
    user_intent: str = "",
    use_narrative_v2: bool = False,
    db_path: str | Path | None = None,
    date_str: str = "",
) -> str:
    logger.info("v2_or_legacy gate: flag=%s gateway=%s db=%s", use_narrative_v2, model_gateway is not None, db_path is not None)
    if use_narrative_v2 and model_gateway is not None and db_path is not None:
        from keypulse.pipeline.narrative_v2 import render_v2_narrative
        v2_body = render_v2_narrative(
            work_blocks,
            model_gateway=model_gateway,
            db_path=db_path,
            date_str=date_str,
        )
        if v2_body:
            return v2_body
        logger.warning("v2 narrative returned empty; falling back to legacy path")
    return _render_daily_narrative_with_llm(
        work_blocks,
        model_gateway=model_gateway,
        evidence_formatter=evidence_formatter,
        user_intent=user_intent,
    )


def _render_daily_narrative_with_llm(
    work_blocks: list[Any],
    *,
    model_gateway: "ModelGateway | None",
    evidence_formatter: Callable[[dict[str, Any]], str] | None,
    user_intent: str = "",
) -> str:
    if model_gateway is not None:
        backend = model_gateway.select_backend("write") if hasattr(model_gateway, "select_backend") else None
        kind = getattr(backend, "kind", "") if backend is not None else ""
        url = getattr(backend, "base_url", "") if backend is not None else ""
        model = getattr(backend, "model", "") if backend is not None else ""
        available = backend is not None and kind and kind != "disabled" and model and url
        if available and hasattr(model_gateway, "render_daily_narrative"):
            try:
                result = model_gateway.render_daily_narrative(list(work_blocks), user_intent=user_intent).strip()
                if result:
                    return result
            except TypeError:
                try:
                    result = model_gateway.render_daily_narrative(list(work_blocks)).strip()
                    if result:
                        return result
                except Exception as exc:
                    logger.error(
                        "obsidian_daily_narrative fallback backend_kind=%s url=%s model=%s exc_type=%s exc=%s",
                        kind, url, model, type(exc).__name__, exc,
                    )
            except Exception as exc:
                logger.error(
                    "obsidian_daily_narrative fallback backend_kind=%s url=%s model=%s exc_type=%s exc=%s",
                    kind, url, model, type(exc).__name__, exc,
                )
    return render_daily_narrative(
        list(work_blocks),
        evidence_formatter=evidence_formatter,
        include_heading=True,
    )


def _source_label(source: str | None) -> str:
    return {
        "manual": "手动保存",
        "clipboard": "剪贴板",
        "window": "窗口活动",
        "ax_text": "当前看到的正文",
        "ocr_text": "屏幕识别补充",
        "keyboard_chunk": "键入整理片段",
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
            return slugify(candidate, fallback="topic")

    return None


def _event_slug(item: dict[str, Any], topic_key: str) -> str:
    title = item.get("title") or item.get("window_title") or item.get("app_name") or topic_key
    return slugify(str(title), fallback=topic_key)


def _hash_suffix(*parts: str) -> str:
    digest = hashlib.sha1("::".join(parts).encode("utf-8")).hexdigest()
    return digest[:8]


def _event_filename(item: dict[str, Any], date_str: str, topic_key: str) -> str:
    title = str(item.get("title") or item.get("window_title") or item.get("app_name") or topic_key or "").strip()
    created_at = str(item.get("created_at") or "")
    if not _is_meaningful_topic(title):
        return f"片段-{time_token(created_at)}-{_hash_suffix(date_str, topic_key, created_at, title)}.md"
    event_slug = _event_slug(item, topic_key)
    return f"{time_token(created_at)}-{event_slug}-{_hash_suffix(date_str, topic_key, created_at, title)}.md"


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


def _obsidian_link(path: str, label: str | None = None) -> str:
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


def _existing_topic_alias(output_path: Path, item: dict[str, Any]) -> str | None:
    tags = str(item.get("tags") or "").strip()
    if not tags:
        return None
    parts = [part.strip() for part in tags.split(",") if part.strip()]
    if len(parts) < 2:
        return None
    candidate = slugify("-".join(parts), fallback="topic")
    topic_path = output_path / "Topics" / f"{candidate}.md"
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
    decisions: list[Any] | None = None,
    evidence_paths: dict[tuple[str, str, str], str] | None = None,
    model_gateway: "ModelGateway | None" = None,
    previous_plan: str = "",
) -> str:
    candidates = snapshot.get("candidates", [])
    filtered_reasons = snapshot.get("filtered_reasons", {})
    theme_candidates = snapshot.get("theme_candidates", [])
    top_block = max((block for block in work_blocks or [] if not getattr(block, "fragment", False)), default=None, key=lambda block: getattr(block, "duration_sec", 0))
    top_theme = getattr(top_block, "theme", "") or _topic_title(str(theme_candidates[0]["topic_key"])) if theme_candidates else "今天"
    decision_items = decisions or []
    decision_brief = " / ".join(getattr(item, "title", "") for item in decision_items[:2] if getattr(item, "title", ""))
    lead_lines = [
        f"> 主战场是 {top_theme}。",
        f"> 今天有 {len(decision_items)} 件事等你拍板：{decision_brief}" if decision_items else "> 今天没有必须拍板的事。",
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
    review_queue = [
        f"- {item.title}：{item.reason}\n  - 命令：{item.command}"
        for item in decision_items[:3]
    ] or ["- 当前没有需要你手动确认的内容"]

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
            "## 💡 需要你决定",
            render_daily_decisions(decision_items, include_heading=False),
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
) -> NoteCard:
    event_summaries: list[str] = []
    for item in topic_items:
        event_link = _obsidian_link(_note_path("event", date_str, topic_key, item.get("created_at")), item["title"])
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
    use_narrative_v2: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    normalized = [item for item in (_to_item(event) for event in items) if item is not None]
    surface_snapshot = build_surface_snapshot(items)
    work_blocks = aggregate_work_blocks(
        normalized,
        sessions=sessions,
        recent_topic_keys=recent_topic_keys,
        previous_day_topic_keys=previous_day_topic_keys,
    )
    decisions = build_daily_decisions(
        work_blocks,
        theme_keys=(recent_topic_keys or set()) | (previous_day_topic_keys or set()),
        recent_topic_counts=recent_topic_counts or {},
    )
    work_item_count = len(work_blocks)
    work_topic_count = len({block.theme for block in work_blocks if not block.fragment})
    top_block = max((block for block in work_blocks if not block.fragment), default=None, key=lambda block: block.duration_sec)
    top_theme = getattr(top_block, "theme", "") or (work_blocks[0].theme if work_blocks else "今天")
    topic_block_counts = Counter(block.theme for block in work_blocks if not block.fragment)

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
            event_card = _build_event_card(item, date_str, topic_key)
            event_cards.append(event_card)
            event_link = _obsidian_link(event_card.path, item["title"])
            evidence_paths[_event_identity(item)] = event_card.path
            daily_links.append(event_link)

        if topic_key == "uncategorized" or topic_block_counts.get(topic_key, 0) < 2:
            continue

        topic_card = _build_topic_card(vault_name, date_str, topic_key, topic_items)
        topic_cards.append(topic_card)
        daily_topic_links.append(_obsidian_link(topic_card.path, _topic_title(topic_key)))

    daily_body = "\n".join(
        [
            f"# {date_str}",
            "",
            *_render_previous_plan_acknowledgment(previous_plan),
            "- 今日工作台：[[Dashboard/Today]]",
            f"- 知识库：{vault_name}",
            f"- 事件卡：{work_item_count}",
            f"- 主题卡：{work_topic_count}",
            "",
            _render_daily_narrative_v2_or_legacy(
                work_blocks,
                model_gateway=model_gateway,
                evidence_formatter=lambda item: _obsidian_link(evidence_paths.get(_event_identity(item), ""), item["title"]) if evidence_paths else item["title"],
                user_intent=previous_plan,
                use_narrative_v2=use_narrative_v2,
                db_path=db_path,
                date_str=date_str,
            ),
            "",
            "## 需要你决定",
            render_daily_decisions(decisions, include_heading=False),
            "",
            "## 今天的事件卡",
            *_preview_links(daily_links, "事件卡"),
            "",
            "## 今天涉及的主题",
            *_preview_links(daily_topic_links, "主题卡"),
            *_render_tomorrow_plan_section(current_plan_existing),
        ]
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

    dashboard_card = _build_note_card(
        "dashboard",
        date_str,
        "dashboard",
        {"created_at": f"{date_str}T00:00:00+00:00"},
        _render_dashboard_body(
            surface_snapshot,
            date_str,
            work_blocks=work_blocks,
            decisions=decisions,
            evidence_paths=evidence_paths,
            model_gateway=model_gateway,
            previous_plan=previous_plan,
        ),
        extra_props={
            "vault": vault_name,
            "candidate_count": len(surface_snapshot.get("candidates", [])),
            "filtered_total": surface_snapshot.get("filtered_total", 0),
            "date": date_str,
            "weekday": _weekday_label(date_str),
            "focus_hours": _format_duration(sum(block.duration_sec for block in work_blocks if not block.fragment)),
            "context_blocks": sum(1 for block in work_blocks if not block.fragment),
            "top_theme": top_theme,
            "needs_decision": len(decisions),
        },
        path="Dashboard/Today.md",
    )

    return {
        "dashboard": [dashboard_card.as_dict()],
        "daily": [daily_card.as_dict()],
        "events": [card.as_dict() for card in event_cards],
        "topics": [card.as_dict() for card in topic_cards],
    }


def _build_event_card(item: dict[str, Any], date_str: str, topic_key: str) -> NoteCard:
    event_path = f"Events/{date_str}/{_event_filename(item, date_str, topic_key)}"
    body = "\n".join(
        [
            f"# {item['title']}",
            "",
            f"- 来源：{_source_label(item['origin_source'])}",
            f"- 应用：{item.get('app_name') or '—'}",
            f"- 置信度：{item['confidence']}",
            f"- 所属主题：{_obsidian_link(_note_path('topic', date_str, topic_key), _topic_title(topic_key))}",
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
    }
    return _build_note_card("event", date_str, topic_key, item, body, extra_props=extra, path=event_path)


def write_obsidian_bundle(bundle: dict[str, list[dict[str, Any]]], output_dir: str | Path) -> list[Path]:
    output_path = Path(output_dir).expanduser()
    written: list[Path] = []
    event_dates = {
        Path(note["path"]).parts[1]
        for note in bundle.get("events", [])
        if len(Path(note["path"]).parts) >= 3
    }
    for date_str in event_dates:
        event_dir = output_path / "Events" / date_str
        if event_dir.exists():
            for stale in event_dir.glob("*.md"):
                stale.unlink()
    for section in ("dashboard", "daily", "events", "topics"):
        for note in bundle.get(section, []):
            relative = Path(note["path"])
            target = output_path / relative
            atomic_write_text(target, render_note(note["properties"], note["body"]))
            written.append(target)
    return written


def _write_note_if_missing(output_path: Path, note: dict[str, Any]) -> Path | None:
    target = output_path / Path(note["path"])
    if target.exists():
        return None
    atomic_write_text(target, render_note(note["properties"], note["body"]))
    return target


def _incremental_dashboard_note(
    date_str: str,
    vault_name: str,
    full_day_raw_events: list[dict[str, Any]],
    *,
    sessions: list[dict[str, Any]],
    previous_plan: str,
    current_plan_existing: str,
    existing_dashboard_text: str,
) -> dict[str, Any] | None:
    full_bundle = build_obsidian_bundle(
        full_day_raw_events,
        vault_name=vault_name,
        date_str=date_str,
        sessions=sessions,
        model_gateway=None,
        previous_plan=previous_plan,
        current_plan_existing=current_plan_existing,
    )
    dashboard_note = dict(full_bundle["dashboard"][0])
    fresh_body = dashboard_note["body"]

    if not existing_dashboard_text:
        return dashboard_note

    if _frontmatter_value(existing_dashboard_text, "date") != date_str:
        return None

    old_main = _extract_block(existing_dashboard_text, "## 🎯 今日主线", "## 💡 需要你决定")
    if old_main:
        fresh_body = _replace_block(fresh_body, "## 🎯 今日主线", "## 💡 需要你决定", old_main)

    old_details = _extract_block(existing_dashboard_text, "<details>", "</details>")
    if old_details:
        fresh_body = _replace_block(fresh_body, "<details>", "</details>", old_details)

    dashboard_note["body"] = fresh_body
    return dashboard_note


def export_obsidian_incremental(
    db_path: str | Path,
    vault_path: str | Path,
    cursor_path: str | Path | None,
    date: str,
    *,
    vault_name: str = "KeyPulse",
) -> list[Path]:
    output_path = Path(vault_path).expanduser()
    date_str = iso_date(date)
    db_path_resolved = Path(db_path).expanduser()
    cursor_file = _sync_cursor_path(cursor_path)
    cursor_state = _read_cursor_state(cursor_file)
    last_event_id = int(cursor_state.get("last_event_id") or 0)

    daily_path = output_path / "Daily" / f"{date_str}.md"
    dashboard_path = output_path / "Dashboard" / "Today.md"
    previous_day = (datetime.fromisoformat(f"{date_str}T00:00:00+00:00") - timedelta(days=1)).date().isoformat()
    previous_plan = _read_tomorrow_plan(output_path / "Daily" / f"{previous_day}.md")
    current_plan_existing = _read_tomorrow_plan(daily_path)
    existing_daily_text = _read_text(daily_path)
    existing_dashboard_text = _read_text(dashboard_path)
    if db_path_resolved.exists():
        window_raw_events = _query_events_by_date(db_path_resolved, date_str, min_id_exclusive=last_event_id)
        full_day_raw_events = _query_events_by_date(db_path_resolved, date_str)
        sessions = _query_sessions_by_date(db_path_resolved, date_str, limit=500)
    else:
        day_since, day_until = local_day_bounds(date_str)
        full_day_raw_events = query_raw_events(since=day_since, until=day_until, limit=5000)
        if any(row.get("id") is not None for row in full_day_raw_events):
            window_raw_events = [row for row in full_day_raw_events if int(row.get("id") or 0) > last_event_id]
        else:
            window_raw_events = list(full_day_raw_events)
        sessions = []
        try:
            from keypulse.store.repository import get_sessions

            sessions = get_sessions(date_str=date_str, limit=500)
        except Exception:
            sessions = []
    written: list[Path] = []

    if not existing_daily_text:
        bundle = build_obsidian_bundle(
            full_day_raw_events,
            vault_name=vault_name,
            date_str=date_str,
            sessions=sessions,
            model_gateway=None,
            previous_plan=previous_plan,
            current_plan_existing=current_plan_existing,
        )
        written = write_obsidian_bundle(bundle, output_path)
        max_new_id = max((int(row.get("id") or 0) for row in window_raw_events), default=last_event_id)
        _write_cursor_state_atomic(
            cursor_file,
            {
                "last_event_id": max(last_event_id, max_new_id),
                "last_run_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return written

    event_items: list[dict[str, Any]] = []
    for raw_event in sorted(window_raw_events, key=lambda row: str(row.get("ts_start") or "")):
        item = _to_item(raw_event)
        if item is not None:
            event_items.append(item)

    existing_event_keys = _section_link_keys(existing_daily_text, "## 今天的事件卡", kind="event")
    existing_topic_keys = _section_link_keys(existing_daily_text, "## 今天涉及的主题", kind="topic")
    topic_items_by_key: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    daily_event_lines: list[str] = []

    for item in event_items:
        event_card = _build_event_card(item, date_str, item["topic_key"])
        event_key = _event_link_key_from_target(event_card.path)
        if event_key in existing_event_keys:
            continue
        existing_event_keys.add(event_key)
        daily_event_lines.append(f"- {_obsidian_link(event_card.path, item['title'])}")
        topic_bucket = _existing_topic_alias(output_path, item) or item["topic_key"]
        topic_items_by_key[topic_bucket].append((item, event_card.path))
        written_path = _write_note_if_missing(output_path, event_card.as_dict())
        if written_path is not None:
            written.append(written_path)

    daily_topic_lines: list[str] = []
    for topic_key, topic_entries in sorted(topic_items_by_key.items()):
        topic_path = output_path / "Topics" / f"{topic_key}.md"
        topic_link = _obsidian_link(f"Topics/{topic_key}.md", _topic_title(topic_key))
        if topic_path.exists():
            topic_text = _read_text(topic_path)
            existing_topic_event_keys = _section_link_keys(topic_text, "## 相关证据", kind="event")
            topic_lines_to_add: list[str] = []

            for item, event_path in topic_entries:
                event_key = _event_link_key_from_target(event_path)
                if event_key in existing_topic_event_keys:
                    continue
                existing_topic_event_keys.add(event_key)
                topic_lines_to_add.append(f"- {_obsidian_link(event_path, item['title'])} - {item['title']}")

            if topic_lines_to_add:
                updated_text = _replace_first_matching_line(
                    topic_text,
                    r"^- 关联片段：\s*\d+\s*$",
                    f"- 关联片段：{len(existing_topic_event_keys)}",
                )
                updated_text = _append_unique_section_lines(updated_text, "## 相关证据", topic_lines_to_add, kind="event")
                _write_text(topic_path, updated_text)
                written.append(topic_path)

            if topic_key not in existing_topic_keys:
                daily_topic_lines.append(f"- {topic_link}")
                existing_topic_keys.add(topic_key)
            continue

        if topic_key == "uncategorized" or len(topic_entries) < 2:
            continue

        topic_items = [item for item, _event_path in topic_entries]
        topic_card = _build_topic_card(vault_name, date_str, topic_key, topic_items)
        written_path = _write_note_if_missing(output_path, topic_card.as_dict())
        if written_path is not None:
            written.append(written_path)
        if topic_key not in existing_topic_keys:
            daily_topic_lines.append(f"- {topic_link}")
            existing_topic_keys.add(topic_key)

    updated_daily = existing_daily_text
    updated_daily = _replace_or_append_top_line(updated_daily, "- 事件卡：", len(existing_event_keys))
    updated_daily = _replace_or_append_top_line(updated_daily, "- 主题卡：", len(existing_topic_keys))
    updated_daily = _append_unique_section_lines(updated_daily, "## 今天的事件卡", daily_event_lines, kind="event")
    updated_daily = _append_unique_section_lines(updated_daily, "## 今天涉及的主题", daily_topic_lines, kind="topic")
    if updated_daily != existing_daily_text:
        _write_text(daily_path, updated_daily)
        written.append(daily_path)

    existing_dashboard_date = _frontmatter_value(existing_dashboard_text, "date")
    if not existing_dashboard_text or existing_dashboard_date == date_str:
        dashboard_note = _incremental_dashboard_note(
            date_str,
            vault_name,
            full_day_raw_events,
            sessions=sessions,
            previous_plan=previous_plan,
            current_plan_existing=current_plan_existing,
            existing_dashboard_text=existing_dashboard_text,
        )
        if dashboard_note is not None:
            target_text = render_note(dashboard_note["properties"], dashboard_note["body"])
            if target_text != existing_dashboard_text:
                _write_text(dashboard_path, target_text)
            written.append(dashboard_path)

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
) -> list[Path]:
    resolved_db_path = Path(db_path).expanduser() if db_path is not None else _default_db_path()
    return export_obsidian_incremental(
        resolved_db_path,
        output_dir,
        cursor_path,
        date_str,
        vault_name=vault_name,
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
    use_narrative_v2: bool = False,
) -> list[Path]:
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
    recent_blocks = aggregate_work_blocks([item for item in (_to_item(event) for event in recent_events) if item is not None])
    recent_topic_keys = {block.theme for block in recent_blocks if not block.fragment}
    recent_topic_counts = Counter(block.theme for block in recent_blocks if not block.fragment)
    previous_blocks = aggregate_work_blocks([item for item in (_to_item(event) for event in previous_day_events) if item is not None])
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
        use_narrative_v2=use_narrative_v2,
        db_path=db_path,
    )
    written = write_obsidian_bundle(bundle, output_dir)
    return written
