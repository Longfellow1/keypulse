from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from keypulse.config import Config
from keypulse.integrations import resolve_active_sink
from keypulse.observability.watcher_tiers import WATCHER_TIERS
from keypulse.pipeline.clustering import (
    build_evidence_graph,
    build_feature_index,
    component_features,
    connected_components,
    detect_merge_candidates,
)
from keypulse.pipeline.daily_strategy import (
    BudgetStrategyDeps,
    BudgetTwoStepStrategy,
    ClusterRecord,
    DailyStrategyError,
    FlagshipSingleStepStrategy,
)
from keypulse.pipeline.daily_summary import (
    build_cluster_stubs_from_narrative,
    build_topic_status_snapshot_from_narrative,
    render_daily_markdown,
    write_daily_summary,
)
from keypulse.pipeline.daily_validator import _DECISION_RE, _OUTPUT_RE
from keypulse.pipeline.anchor_gateway import AnchorGateway, AnchorGatewayError
from keypulse.pipeline.artifact_writer import write_artifact
from keypulse.pipeline.event_intake import cap_events_by_source
from keypulse.pipeline.llm_errors import classify_llm_error
from keypulse.pipeline.weekly_topic_anchor import (
    anchor_today_clusters,
    load_weekly_anchors,
    seed_w19_anchors,
    save_weekly_anchors,
    split_topics_and_unanchored,
    update_anchors_with_assignments,
)
from keypulse.pipeline.model import LLMCallError, ModelGateway, load_model_gateway
from keypulse.pipeline.model_card import resolve_tier
from keypulse.pipeline.run_record import RunRecorder
from keypulse.store.repository import query_raw_events
from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.dates import local_day_bounds, local_timezone
from keypulse.utils.paths import get_data_dir


_TRIGGER_VALUES = {"18:00", "23:30"}
_INPUT_MARKER_BEGIN = "<<INPUT_JSON>>"
_INPUT_MARKER_END = "<<END_INPUT_JSON>>"
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,29}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_TOKENISH_RE = re.compile(r"[a-zA-Z0-9_./:-]+")
_H3_HEADING_RE = re.compile(r"^###\s+", re.MULTILINE)
_TOPIC_SENTENCE_SPLIT_RE = re.compile(r"[。；;.!?\n]+")
# === OCR watcher 已下线 2026-05-14 ===
# 原因：日均 9 条 / 权重 0.5 / macOS Vision 绑死 / 屏幕录制权限门槛高 / 键盘+AX+clipboard 已覆盖
# 回退方法：移除本块注释 + 恢复 manager.py 里 OCR 调度分支
# 历史 raw_events 中 ocr_text_capture 数据保留可读
_TOOL_ECHO_SOURCES = frozenset({"idle", "knowledgec", "zsh_history"})
_CONTEXT_SOURCES = frozenset({"window", "ax_text"})
_USER_MESSAGE_SOURCES = frozenset({"clipboard", "manual", "markdown_vault", "claude_code", "codex_cli", "keyboard_chunk", "browser_url"})
_FLAGSHIP_EVENT_LIMIT = 60

_logger = logging.getLogger(__name__)


class DailyOrchestratorError(RuntimeError):
    """Raised when daily orchestration cannot complete and caller should fallback."""


@dataclass(frozen=True)
class DailySummary:
    date: str
    trigger: str
    event_count: int
    processed_count: int
    cluster_count: int
    misc_event_ids: tuple[str, ...]
    topic_diffs: tuple[str, ...]
    daily_path: str
    summary_path: str
    skipped: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_local_datetime(ts_iso: str) -> datetime | None:
    text = str(ts_iso or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(local_timezone())


def _to_local_hhmm(ts_iso: str) -> str:
    local_dt = _to_local_datetime(ts_iso)
    return local_dt.strftime("%H:%M") if local_dt is not None else ""


def _to_local_mmdd_hhmm(ts_iso: str) -> str:
    local_dt = _to_local_datetime(ts_iso)
    return local_dt.strftime("%m-%d %H:%M") if local_dt is not None else ""


def _parse_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata_json")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _extract_event_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _parse_metadata(row)
    entities_raw = metadata.get("entities")
    entities = dict(entities_raw) if isinstance(entities_raw, dict) else {}

    event_id = str(row.get("id") or "").strip()
    if not event_id:
        raise ValueError("raw event row missing id")

    ts_start = str(row.get("ts_start") or "").strip()
    if not ts_start:
        raise ValueError(f"raw event {event_id} missing ts_start")

    app_name = str(row.get("app_name") or metadata.get("app_name") or "unknown").strip() or "unknown"
    content_text = str(row.get("content_text") or "").strip()
    window_title = str(row.get("window_title") or metadata.get("window_title") or "").strip()

    payload = dict(row)
    payload.update(
        {
            "id": event_id,
            "ts_start": ts_start,
            "source": str(row["source"] or "").strip(),
            "event_type": str(row.get("event_type") or "").strip(),
            "speaker": str(row["speaker"] or "").strip(),
            "app_name": app_name,
            "window_title": window_title,
            "process_name": str(row.get("process_name") or "").strip(),
            "content_text": content_text,
            "ts_end": row.get("ts_end"),
            "content_hash": row.get("content_hash"),
            "session_id": str(row.get("session_id") or entities.get("session_id") or "").strip(),
            "semantic_weight": row.get("semantic_weight"),
            "user_present": row.get("user_present"),
            "metadata_json": json.dumps({**metadata, "entities": entities}, ensure_ascii=False),
        }
    )
    return payload


def _build_prompt(spec_body: str, capability: str, payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    return "\n".join(
        [
            f"CAPABILITY: {capability}",
            spec_body.strip(),
            _INPUT_MARKER_BEGIN,
            rendered,
            _INPUT_MARKER_END,
        ]
    )


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


def _keywords_from_text(text: str) -> list[str]:
    tokens = _WORD_RE.findall(text.lower())
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def _estimate_token_count(text: str) -> int:
    if not text:
        return 0
    cjk_count = len(_CJK_RE.findall(text))
    tokenish_count = len(_TOKENISH_RE.findall(text))
    return cjk_count + tokenish_count


def _source_kind(event: Mapping[str, Any]) -> str:
    speaker = str(event.get("speaker") or "").strip().lower()
    source = str(event.get("source") or "").strip().lower()
    if speaker in {"ai", "assistant"}:
        return "assistant_msg"
    if source in _CONTEXT_SOURCES:
        return "context"
    if speaker == "user" or source in _USER_MESSAGE_SOURCES:
        return "user_msg"
    if source in _TOOL_ECHO_SOURCES or speaker == "system":
        return "tool_echo"
    return "default"


def _event_value_density(event: Mapping[str, Any], settings: Any | None = None) -> float:
    cfg = settings or Config().pipeline.value_density
    if not bool(getattr(cfg, "enabled", True)):
        return 0.0

    text = " ".join(
        str(part or "").strip()
        for part in (event.get("content_text"), event.get("window_title"))
        if str(part or "").strip()
    )
    token_target = max(int(getattr(cfg, "token_target", 80) or 80), 1)
    base = min(_estimate_token_count(text) / token_target, 1.0)
    weights = getattr(cfg, "source_weights", {}) or {}
    kind = _source_kind(event)
    fallback_weight = 0.85 if kind == "context" else weights.get("default", 0.6)
    weight = float(weights.get(kind, fallback_weight) or 0.0)
    score = base * max(weight, 0.0)

    decision_regex = str(getattr(cfg, "decision_regex", "") or "").strip()
    if decision_regex:
        try:
            if re.search(decision_regex, text):
                score += max(float(getattr(cfg, "decision_bonus", 0.0) or 0.0), 0.0)
        except re.error:
            pass

    return round(min(max(score, 0.0), 1.0), 4)


def _component_size_score(event_count: int) -> float:
    return round(min(max(event_count, 0) / 5.0, 0.7), 4)


def _component_density_metadata(component_events: list[dict[str, Any]], settings: Any | None = None) -> dict[str, float]:
    densities = [_event_value_density(event, settings) for event in component_events]
    peak = max(densities, default=0.0)
    size_score = _component_size_score(len(component_events))
    return {
        "size_score": size_score,
        "peak_event_density": peak,
    }


def _flagship_event_score(event: Mapping[str, Any]) -> float:
    text = " ".join(
        str(part or "").strip()
        for part in (event.get("content_text"), event.get("window_title"), event.get("app_name"))
        if str(part or "").strip()
    )
    score = _event_value_density(event)
    if _CJK_RE.search(text):
        score += 0.3
    if 12 <= len(str(event.get("content_text") or "").strip()) <= 260:
        score += 0.15
    if _source_kind(event) == "context":
        win = str(event.get("window_title") or "")
        if win and (" - " in win or " — " in win or " – " in win):
            score += 0.4
    return round(score, 4)


def _event_hour_key(event: Mapping[str, Any]) -> str:
    ts = str(event.get("ts_start") or "").strip()
    if len(ts) >= 13 and ts[10] == "T":
        return ts[:13]
    return ""


def _cap_flagship_events_for_prompt(
    events: list[dict[str, Any]],
    *,
    limit: int = _FLAGSHIP_EVENT_LIMIT,
) -> tuple[list[dict[str, Any]], bool]:
    scored_events: list[dict[str, Any]] = []
    for event in events:
        normalized = dict(event)
        normalized["_flagship_score"] = _flagship_event_score(event)
        scored_events.append(normalized)
    return cap_events_by_source(scored_events, limit=limit)


def _slugify_topic(text: str, *, fallback: str) -> str:
    tokens = _keywords_from_text(text)
    if not tokens:
        tokens = _keywords_from_text(fallback) or ["topic", "new"]
    slug = "-".join(tokens[:5]).strip("-")
    if not slug:
        slug = fallback
    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if not slug:
        slug = "topic-new"
    if not slug[0].isalpha():
        slug = f"t-{slug}"
    return slug[:41].rstrip("-")


def _mock_l1_output(payload: dict[str, Any]) -> dict[str, Any]:
    components = payload.get("components") or []
    topics = payload.get("existing_topics_index") or []
    hot = set(payload.get("hot_cache") or [])
    topic_map: dict[str, dict[str, Any]] = {
        str(item.get("slug")): item for item in topics if isinstance(item, dict) and str(item.get("slug") or "").strip()
    }

    output_clusters: list[dict[str, Any]] = []
    misc_event_ids: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        component_id = str(component.get("component_id") or "")
        event_ids = [str(item) for item in (component.get("event_ids") or []) if str(item).strip()]
        keywords = [str(item).lower() for item in (component.get("keywords") or []) if str(item).strip()]

        if any("misc" in word for word in keywords):
            output_clusters.append(
                {
                    "component_id": component_id,
                    "topic_action": "misc",
                    "reason": "misc keyword matched",
                }
            )
            misc_event_ids.extend(event_ids)
            continue

        hit_slug = None
        best_score = -1
        for slug, topic in topic_map.items():
            topic_keywords = [str(item).lower() for item in (topic.get("keywords") or []) if str(item).strip()]
            overlap = len(set(keywords) & set(topic_keywords))
            score = overlap + (2 if slug in hot and overlap > 0 else 0)
            if score > best_score and score > 0:
                best_score = score
                hit_slug = slug

        if hit_slug:
            output_clusters.append(
                {
                    "component_id": component_id,
                    "topic_action": "existing",
                    "topic_slug": hit_slug,
                    "reason": "keyword overlap with existing topic",
                }
            )
            continue

        output_clusters.append(
            {
                "component_id": component_id,
                "topic_action": "new",
                "reason": "no existing topic overlap",
            }
        )

    return {"clusters": output_clusters, "misc_event_ids": misc_event_ids}


def _mock_l2_output(payload: dict[str, Any]) -> dict[str, str]:
    date_str = str(payload.get("date") or "unknown-date")
    clusters = [item for item in (payload.get("clusters") or []) if isinstance(item, dict)]
    lines = [
        "📍 Asia/Shanghai",
        "",
        f"# {date_str}",
        "",
        "## 今日要点",
        "",
        "你今天把分散事件收拢成可复盘的主题叙事，重点不是操作数量，而是确认了哪些工作线索值得沉淀，以及哪些噪音可以被排除在日报主体之外。",
        "",
        "## 今天做的事",
        "",
    ]
    if not clusters:
        lines.extend(["### 日常推进", "", "你围绕同一条工作线持续推进，留下了足够的上下文用于回看。"])
    for cluster in clusters:
        display_name = str(cluster.get("display_name") or "日常推进")
        events = [item for item in (cluster.get("events") or []) if isinstance(item, dict)]
        content = "；".join(str(item.get("c") or "").strip() for item in events[:2]) or "处理关键任务"
        lines.extend(
            [
                f"### {display_name}",
                "",
                f"你围绕「{display_name}」推进了连续事项，输入里能看到 {content}。这组事件形成了相对完整的上下文，适合沉淀为主题而不是散点记录。",
                "",
            ]
        )
    lines.extend(["## 明日的锚点", "", "> 明天我想：______", ">", "> _写一句话留给明天的自己_", ""])
    return {"markdown": "\n".join(lines)}


def _mock_daily_flagship_output(payload: dict[str, Any]) -> dict[str, str]:
    return _mock_l2_output({"date": payload.get("date"), "clusters": [{"display_name": "全天主线", "events": payload.get("events") or []}], "misc_events": []})


def _mock_l0_anchor_output(payload: dict[str, Any]) -> dict[str, Any]:
    assignments = {}
    for cluster in payload.get("today_clusters") or []:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("cluster_id") or "").strip()
        if cluster_id:
            assignments[cluster_id] = "unanchored"
    return {"assignments": assignments, "new_anchors": []}


def _mock_l3_output(payload: dict[str, Any]) -> dict[str, Any]:
    events = payload.get("events") or []
    existing = {str(item) for item in (payload.get("existing_slugs") or [])}
    content = " ".join(str(item.get("content") or "") for item in events if isinstance(item, dict))
    slug = _slugify_topic(content, fallback="topic-new")
    if slug in existing:
        index = 2
        candidate = f"{slug}-{index}"
        while candidate in existing:
            index += 1
            candidate = f"{slug}-{index}"
        slug = candidate

    keywords = _keywords_from_text(content)
    while len(keywords) < 5:
        keywords.append(f"kw{len(keywords)+1}")
    keywords = keywords[:10]
    return {
        "slug": slug,
        "display_name": f"{slug.replace('-', ' ').title()}",
        "keywords": keywords[:10],
    }


def _install_mock_backend(gateway: ModelGateway) -> None:
    fail_budget = int(os.environ.get("MOCK_LLM_FAILS", "0") or 0)
    fail_counter = {"count": 0}

    def fake_call_capability_backend(**kwargs):
        if fail_counter["count"] < fail_budget:
            fail_counter["count"] += 1
            raise RuntimeError("mock llm forced failure")

        prompt = str(kwargs.get("prompt") or "")
        capability, payload = _extract_prompt_payload(prompt)
        if capability == "L1_cluster_review":
            body = json.dumps(_mock_l1_output(payload), ensure_ascii=False)
        elif capability == "L2_narrative":
            body = json.dumps(_mock_l2_output(payload), ensure_ascii=False)
        elif capability == "L3_topic_naming":
            body = json.dumps(_mock_l3_output(payload), ensure_ascii=False)
        elif capability == "L0_anchor":
            body = json.dumps(_mock_l0_anchor_output(payload), ensure_ascii=False)
        elif capability == "daily_flagship":
            body = json.dumps(_mock_daily_flagship_output(payload), ensure_ascii=False)
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


def _state_path() -> Path:
    return get_data_dir() / "daily-orchestrator-state.json"


def _read_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"dates": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"dates": {}}
    if isinstance(payload, dict):
        return payload
    return {"dates": {}}


def _write_state(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _sync_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if now.tzinfo is not None:
        return now.astimezone(timezone.utc).replace(tzinfo=None)
    return now


def _parse_trigger_ts(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _last_daily_sync_success_at(date_str: str, db_path: Path) -> datetime | None:
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ts_utc
            FROM llm_trigger_log
            WHERE kind='T2'
              AND outcome='ran:ok'
              AND note LIKE ?
            ORDER BY ts_utc DESC
            LIMIT 1
            """,
            (f"%{date_str}%",),
        )
        row = cursor.fetchone()
        conn.close()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return _parse_trigger_ts(str(row[0] or ""))


def maybe_run_daily_for_sync(date_str: str, db_path: Path, *, now: datetime | None = None) -> tuple[bool, str]:
    """Decide if daily orchestrator should run after Obsidian sync."""

    active_now = _sync_now(now)
    try:
        from keypulse.pipeline.triggers import record_trigger, should_trigger

        allowed, reason = should_trigger("T1", now=active_now, db_path=db_path, cfg={})
        if not allowed:
            if reason.startswith("error:"):
                _logger.error("daily_orchestrator skipped via obsidian sync: %s", reason)
                return False, reason
            _logger.info("daily_orchestrator skipped via obsidian sync: %s", reason)
            return False, "skip:no_activity"

        last_success = _last_daily_sync_success_at(date_str, db_path)
        if last_success is not None and active_now - last_success < timedelta(minutes=15):
            _logger.info("daily_orchestrator skipped via obsidian sync: daily:dedupe_15min")
            record_trigger(
                "T2",
                now=active_now,
                db_path=db_path,
                outcome="skipped:daily:dedupe_15min",
                note=f"daily_orchestrator:{date_str}",
            )
            return False, "skip:dedupe_15min"

        run_daily(date_str, trigger="18:00")
        record_trigger(
            "T2",
            now=active_now,
            db_path=db_path,
            outcome="ran:ok",
            note=f"daily_orchestrator:{date_str}",
        )
        return True, "ran:ok"
    except Exception as exc:
        reason = f"error:{type(exc).__name__}:{exc}"
        _logger.error("daily_orchestrator failed via obsidian sync: %s", reason)
        try:
            from keypulse.pipeline.triggers import record_trigger

            record_trigger(
                "T2",
                now=active_now,
                db_path=db_path,
                outcome="ran:fail",
                note=f"daily_orchestrator:{date_str}:{reason}",
            )
        except Exception:
            pass
        return False, reason


def run_daily_after_obsidian_sync(
    date_str: str,
    *,
    db_path: Path,
    now: datetime | None = None,
    min_interval_minutes: int = 30,
    should_trigger_fn: Callable[..., tuple[bool, str]] | None = None,
    run_daily_fn: Callable[..., Any] | None = None,
    logger_fn: Callable[[str], None] | None = None,
) -> bool:
    """Run daily v3 from Obsidian sync when activity and dedupe gates allow it."""

    if should_trigger_fn is None and run_daily_fn is None:
        ran, reason = maybe_run_daily_for_sync(date_str, db_path, now=now)
        if ran:
            _logger.info("daily_orchestrator ran via obsidian sync: %s", reason)
        return ran

    active_now = _sync_now(now)
    try:
        trigger_check = should_trigger_fn
        if trigger_check is None:
            from keypulse.pipeline.triggers import should_trigger as trigger_check

        allowed, reason = trigger_check("T1", now=active_now, db_path=db_path, cfg={})
        if not allowed:
            _logger.info("daily_after_obsidian_sync skipped date=%s reason=%s", date_str, reason)
            return False

        runner = run_daily_fn or run_daily
        runner(date_str, trigger="18:00")
        return True
    except Exception as exc:
        message = f"daily_after_obsidian_sync failed date={date_str} exc={type(exc).__name__}:{exc}"
        if logger_fn is not None:
            logger_fn(message)
        _logger.error(message)
        return False


def _load_topic_index() -> list[dict[str, Any]]:
    topics_dir = get_data_dir() / "topics"
    if not topics_dir.exists():
        return []
    result: list[dict[str, Any]] = []
    for topic_path in sorted(topics_dir.glob("*.md")):
        text = topic_path.read_text(encoding="utf-8")
        slug = topic_path.stem
        display_name = slug
        keywords: list[str] = []
        last_seen = datetime.fromtimestamp(topic_path.stat().st_mtime, tz=timezone.utc).date().isoformat()

        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            idx = 1
            in_keywords = False
            while idx < len(lines):
                stripped = lines[idx].strip()
                idx += 1
                if stripped == "---":
                    break
                if stripped.startswith("display_name:"):
                    display_name = stripped.split(":", 1)[1].strip().strip('"').strip("'") or display_name
                elif stripped.startswith("last_seen:"):
                    value = stripped.split(":", 1)[1].strip()
                    if value:
                        last_seen = value
                elif stripped.startswith("keywords:"):
                    inline = stripped.split(":", 1)[1].strip()
                    if inline.startswith("[") and inline.endswith("]"):
                        for item in inline.strip("[]").split(","):
                            keyword = item.strip().strip('"').strip("'").lower()
                            if keyword:
                                keywords.append(keyword)
                        in_keywords = False
                    else:
                        in_keywords = True
                elif in_keywords and stripped.startswith("-"):
                    keyword = stripped.lstrip("-").strip().strip('"').strip("'").lower()
                    if keyword:
                        keywords.append(keyword)
                else:
                    in_keywords = False

        if not keywords:
            keywords = _keywords_from_text(display_name)[:5]
        result.append(
            {
                "slug": slug,
                "display_name": display_name,
                "keywords": keywords[:10],
                "last_seen": last_seen,
                "status": "active",
            }
        )
    return result


def _load_hot_slugs() -> list[str]:
    hot_path = get_data_dir() / "hot.md"
    if not hot_path.exists():
        return []
    slugs: list[str] = []
    for line in hot_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        token = stripped[2:].split("|", 1)[0].strip()
        if token:
            slugs.append(token)
    return slugs


def _prune_topics(topics: list[dict[str, Any]], hot_slugs: list[str], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hot_set = set(hot_slugs)
    event_keywords = set()
    for event in events:
        event_keywords.update(_keywords_from_text(str(event.get("content_text") or "")))

    scored: list[tuple[int, dict[str, Any]]] = []
    for topic in topics:
        keywords = {str(item).lower() for item in (topic.get("keywords") or [])}
        overlap = len(keywords & event_keywords)
        score = overlap + (5 if topic.get("slug") in hot_set else 0)
        if score > 0:
            scored.append((score, topic))

    scored.sort(key=lambda item: item[0], reverse=True)
    pruned = [item[1] for item in scored[:30]]
    return pruned


def _component_time_range(component_events: list[dict[str, Any]]) -> tuple[str, str]:
    times: list[datetime] = []
    for event in component_events:
        ts = str(event.get("ts_start") or "")
        parsed = _to_local_datetime(ts)
        if parsed is not None:
            times.append(parsed)
    if not times:
        return "00:00", "23:59"
    times.sort()
    start = times[0].strftime("%H:%M")
    end = times[-1].strftime("%H:%M")
    return start, end


def _component_activity_metadata(component_events: list[dict[str, Any]]) -> dict[str, Any]:
    event_times: list[datetime] = []
    app_windows: dict[str, list[datetime]] = {}
    app_names: set[str] = set()
    key_excerpts: list[str] = []

    for event in component_events:
        parsed = _to_local_datetime(str(event.get("ts_start") or ""))
        app = str(event.get("app_name") or "").strip()
        window = str(event.get("window_title") or "").strip()
        if parsed is not None:
            event_times.append(parsed)
            if app or window:
                app_windows.setdefault(f"{app}\n{window}", []).append(parsed)
        if app:
            app_names.add(app)
        excerpt = " ".join(str(event.get("content_text") or "").split()).strip()
        if excerpt and len(key_excerpts) < 3:
            key_excerpts.append(excerpt[:80])

    dwell_minutes = 0.0
    if event_times:
        dwell_minutes = round((max(event_times) - min(event_times)).total_seconds() / 60, 2)

    revisit_count = 0
    for times in app_windows.values():
        ordered = sorted(times)
        revisit_count += sum(
            1
            for previous, current in zip(ordered, ordered[1:])
            if (current - previous).total_seconds() >= 600
        )

    return {
        "dwell_minutes": dwell_minutes,
        "revisit_count": revisit_count,
        "cross_app_count": len(app_names),
        "key_excerpts": key_excerpts,
    }


def _cluster_component_payloads(
    scoped_events: list[dict[str, Any]],
    density_settings: Any | None,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    graph = build_evidence_graph(scoped_events)
    components = connected_components(graph)
    feature_index = build_feature_index(scoped_events)
    raw_merge_candidates = detect_merge_candidates(components, 0.2, feature_index=feature_index)
    component_ids = [f"c{index+1}" for index in range(len(components))]
    signature_to_component_id = {
        ",".join(sorted(component)): component_ids[index] for index, component in enumerate(components)
    }
    merge_pairs = [
        (signature_to_component_id[left], signature_to_component_id[right])
        for left, right in raw_merge_candidates
        if left in signature_to_component_id and right in signature_to_component_id
    ]

    payloads: list[dict[str, Any]] = []
    for component_id, component in zip(component_ids, components, strict=False):
        event_ids = sorted(list(component))
        event_id_set = set(event_ids)
        component_events = [event for event in scoped_events if str(event.get("id")) in event_id_set]
        feature = component_features(event_id_set, feature_index)
        payloads.append(
            {
                "component_id": component_id,
                "event_ids": event_ids,
                "event_count": len(event_ids),
                "time_range": list(_component_time_range(component_events)),
                "h1_entities": sorted(feature["entities"]),
                "h2_contexts": [],
                "keywords": sorted(feature["keywords"])[:20],
                **_component_density_metadata(component_events, density_settings),
                **_component_activity_metadata(component_events),
            }
        )
    return payloads, merge_pairs


def _topic_display_name(topic_slug: str, topics_index: list[dict[str, Any]]) -> str:
    for item in topics_index:
        if str(item.get("slug")) == topic_slug:
            display = str(item.get("display_name") or "").strip()
            if display:
                return display
    return topic_slug.replace("-", " ")


def _event_link(date_str: str, event_id: str) -> str:
    return f"[[../.keypulse/events/{date_str}/{event_id}|{event_id}]]"


def _upsert_topic(
    *,
    date_str: str,
    trigger: str,
    slug: str,
    display_name: str,
    keywords: list[str],
    narrative: str,
    event_ids: list[str],
) -> str:
    topics_dir = get_data_dir() / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    path = topics_dir / f"{slug}.md"
    now_date = date_str

    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    first_seen = now_date
    if existing_text:
        for line in existing_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("first_seen:"):
                first_seen = stripped.split(":", 1)[1].strip() or now_date
                break

    keyword_values = []
    seen: set[str] = set()
    for item in keywords:
        normalized = str(item).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        keyword_values.append(normalized)
    if len(keyword_values) < 5:
        keyword_values.extend([f"kw{i}" for i in range(len(keyword_values) + 1, 6)])
    keyword_values = keyword_values[:10]

    entry_line = f"- {date_str} {trigger} | {len(event_ids)} events | {narrative}"
    evidence_lines = [f"- {_event_link(date_str, event_id)}" for event_id in event_ids]

    if existing_text:
        lines = existing_text.splitlines()
        if entry_line not in existing_text:
            lines.append(entry_line)
        for evidence in evidence_lines:
            if evidence not in existing_text:
                lines.append(evidence)
        body = "\n".join(lines).rstrip() + "\n"
    else:
        body = "\n".join(
            [
                "---",
                "type: topic",
                f"slug: {slug}",
                f"display_name: {display_name}",
                f"first_seen: {first_seen}",
                f"last_seen: {now_date}",
                "keywords:",
                *[f"  - {item}" for item in keyword_values],
                "---",
                "",
                f"# {display_name}",
                "",
                "## Entries",
                entry_line,
                "",
                "## Related Events",
                *evidence_lines,
                "",
            ]
        )

    # typed diff: update mutable keys in place
    body = re.sub(r"(?m)^display_name:\s*.*$", f"display_name: {display_name}", body)
    body = re.sub(r"(?m)^last_seen:\s*.*$", f"last_seen: {now_date}", body)

    atomic_write_text(path, body)
    return "updated" if existing_text else "created"


def _refresh_hot(topics_touched: list[tuple[str, str]], date_str: str) -> None:
    hot_path = get_data_dir() / "hot.md"
    existing: dict[str, str] = {}
    if hot_path.exists():
        for line in hot_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            token = stripped[2:]
            slug, _, tail = token.partition("|")
            slug = slug.strip()
            if not slug:
                continue
            existing[slug] = tail.strip() or date_str
    for slug, _display in topics_touched:
        existing[slug] = date_str

    lines = ["# hot topics", ""]
    for slug, last_seen in sorted(existing.items(), key=lambda item: item[1], reverse=True)[:50]:
        lines.append(f"- {slug} | {last_seen}")
    lines.append("")
    atomic_write_text(hot_path, "\n".join(lines))


def _append_log(record: dict[str, Any]) -> None:
    log_path = get_data_dir() / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _daily_path(date_str: str) -> Path:
    sink = resolve_active_sink(Config.load(), persist=False)
    target = sink.output_dir / "Daily" / f"{date_str}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _backend_name_for_tier(backend_model: str, cfg: Config) -> str:
    if backend_model == cfg.model.cloud.model:
        return "cloud"
    if backend_model == cfg.model.local.model:
        return "local"
    return "cloud"


def _resolve_daily_tier(gateway: ModelGateway) -> str:
    backend = gateway.select_backend(stage="write")
    cfg = Config.load()
    backend_name = _backend_name_for_tier(backend.model, cfg)
    backend_cfg = getattr(cfg.model, backend_name)
    tier_override = getattr(backend_cfg, "tier", "")
    return resolve_tier(backend.model, override=tier_override)


def _extract_narrative_one_line(markdown: str, display_name: str) -> str:
    lines = markdown.splitlines()
    target_heading = f"### {display_name}".strip()
    in_section = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            if in_section:
                break
            in_section = stripped == target_heading
            continue
        if in_section and stripped and not stripped.startswith("#"):
            collected.append(stripped.lstrip("> ").strip())
    return " ".join(collected)[:120]


def _count_h3_headings(markdown: str) -> int:
    return len(_H3_HEADING_RE.findall(str(markdown or "")))


def _build_flagship_repair_hint(*, before_things: int, component_count: int, minimum_things: int = 3) -> str:
    lines = [
        f"上一次输出仅有 {before_things} 个 `###` 主题段，低于最低要求 {minimum_things}。",
        f"本次输入 components_count={component_count}。",
        "请重写整篇 `markdown`（不要局部补丁），保持事实准确且保留明日锚点占位符。",
        "硬约束：若 components_count >= 3，必须输出至少 3 个 `###` 主题段。",
        "若你收到 `REPAIR MODE`，必须把本次输出视为重写任务而不是增量修补。",
    ]
    return "\n".join(lines)


def _cost_snapshot(run_started_at: datetime) -> dict[str, Any]:
    path = get_data_dir() / "cost.jsonl"
    if not path.exists():
        return {"in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0}

    in_tokens = 0
    out_tokens = 0
    cost_usd = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ts_text = str(payload.get("ts") or "")
        try:
            ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts < run_started_at:
            continue
        in_tokens += int(payload.get("in_tokens") or 0)
        out_tokens += int(payload.get("out_tokens") or 0)
        cost_usd += float(payload.get("cost_usd") or 0.0)
    return {"in_tokens": in_tokens, "out_tokens": out_tokens, "cost_usd": round(cost_usd, 8)}


def _load_rows_for_date(date_str: str) -> list[dict[str, Any]]:
    since, until = local_day_bounds(date_str)
    rows = query_raw_events(since=since, until=until, limit=50000)
    return sorted(rows, key=lambda item: (str(item.get("ts_start") or ""), int(item.get("id") or 0)))


def _core_watcher_emit_counts_for_date(date_str: str, rows: list[dict[str, Any]]) -> dict[str, int]:
    since, until = local_day_bounds(date_str)
    counts: dict[str, int] = {}
    for source in WATCHER_TIERS:
        try:
            counts[source] = len(query_raw_events(source=source, since=since, until=until, limit=50000))
        except Exception:
            counts[source] = sum(1 for row in rows if str(row.get("source") or "") == source)
    return counts


def _filter_for_trigger(date_str: str, trigger: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    state = _read_state()
    dates = state.setdefault("dates", {})
    day_state = dates.setdefault(date_str, {})

    if trigger == "18:00":
        if rows:
            day_state["last_1800_event_id"] = int(rows[-1].get("id") or 0)
        _write_state(state)
        return rows

    cutoff = int(day_state.get("last_1800_event_id") or 0)
    pending = [row for row in rows if int(row.get("id") or 0) > cutoff]
    return pending


def _maybe_trigger_weekly_after_daily(date_str: str) -> None:
    """周五下午跑完日报后，尝试触发 weekly（weekly 内部会按阈值判定是否跳过）。"""
    try:
        day = date_cls.fromisoformat(str(date_str).strip())
    except ValueError:
        _logger.warning("weekly_auto_trigger=skip invalid_date=%s", date_str)
        return

    if day.weekday() != 4:
        return
    if datetime.now().hour < 17:
        return

    iso = day.isocalendar()
    week_str = f"{iso.year}-W{iso.week:02d}"
    _logger.info("weekly_auto_trigger=attempt date=%s week=%s", date_str, week_str)

    try:
        from keypulse.pipeline.weekly_orchestrator import run_weekly

        output_path = run_weekly(week_str)
        if output_path:
            _logger.info("weekly_auto_trigger=ok week=%s weekly_path=%s", week_str, output_path)
        else:
            _logger.info("weekly_auto_trigger=skipped week=%s", week_str)
    except Exception:
        _logger.exception("weekly_auto_trigger=error week=%s", week_str)


def _fallback_summary_clusters_from_events(date_str: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        return [
            {
                "slug": "no-events",
                "display_name": "无事件记录",
                "narrative_one_line": f"{date_str} 当天没有采集到可用事件。",
                "event_count": 1,
                "time_range": ["00:00", "23:59"],
                "merge_candidate_with": [],
            }
        ]

    times: list[str] = []
    for event in events:
        ts = str(event.get("ts_start") or "")
        local_hhmm = _to_local_hhmm(ts)
        if local_hhmm:
            times.append(local_hhmm)
    time_range = [min(times), max(times)] if times else ["00:00", "23:59"]
    return [
        {
            "slug": "low-volume-events",
            "display_name": "低样本事件",
            "narrative_one_line": f"{date_str} 仅有 {len(events)} 条事件，已记录为低样本主线。",
            "event_count": max(1, len(events)),
            "time_range": time_range,
            "merge_candidate_with": [],
        }
    ]


def _ensure_summary_clusters(date_str: str, markdown: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stubs = build_cluster_stubs_from_narrative(date_str, markdown)
    if stubs:
        return stubs
    return _fallback_summary_clusters_from_events(date_str, events)


def _week_str_from_date(date_str: str) -> str:
    day = date_cls.fromisoformat(date_str)
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _summary_clusters_to_anchor_clusters(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cluster_id = str(cluster.get("slug") or cluster.get("cluster_id") or "").strip()
        if not cluster_id:
            continue
        normalized.append(
            {
                "cluster_id": cluster_id,
                "display_name": str(cluster.get("display_name") or cluster_id).strip(),
                "narrative_one_line": str(cluster.get("narrative_one_line") or "").strip(),
                "event_count": int(cluster.get("event_count") or 0),
                "time_range": list(cluster.get("time_range") or ["00:00", "23:59"]),
                "peak_event_density": float(cluster.get("peak_event_density") or 0.0),
            }
        )
    return normalized


def _unique_keep_order(items: list[str], limit: int = 3) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _infer_decisions_shipped(narrative: str) -> tuple[list[str], list[str]]:
    sentences = [part.strip() for part in _TOPIC_SENTENCE_SPLIT_RE.split(str(narrative or "")) if part.strip()]
    decisions: list[str] = []
    shipped: list[str] = []
    for sentence in sentences:
        if _DECISION_RE.search(sentence) or re.search(r"决定不|放弃.+转.+|从.+切到.+|选.+而不是.+|最终采用|回滚到|敲定|拍板", sentence):
            decisions.append(sentence)
        if _OUTPUT_RE.search(sentence):
            shipped.append(sentence)
    decisions = _unique_keep_order(decisions, limit=3)
    shipped = _unique_keep_order(shipped, limit=3)
    if not decisions and not shipped and sentences:
        decisions = [sentences[0]]
    return decisions, shipped


def _run_anchor_for_clusters(
    *,
    date_str: str,
    today_clusters: list[dict[str, Any]],
    gateway: ModelGateway,
    recorder: RunRecorder | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if recorder is None:
        week_str = _week_str_from_date(date_str)
        weekly_anchors = load_weekly_anchors(week_str)
        day = date_cls.fromisoformat(date_str)
        if not weekly_anchors and date_cls(2026, 5, 4) <= day <= date_cls(2026, 5, 10):
            weekly_anchors = seed_w19_anchors()
            save_weekly_anchors(week_str, weekly_anchors)
    else:
        with recorder.stage("anchor_load", failure_reason="weekly_anchor_decode_failed"):
            week_str = _week_str_from_date(date_str)
            weekly_anchors = load_weekly_anchors(week_str)
            day = date_cls.fromisoformat(date_str)
            if not weekly_anchors and date_cls(2026, 5, 4) <= day <= date_cls(2026, 5, 10):
                weekly_anchors = seed_w19_anchors()
                save_weekly_anchors(week_str, weekly_anchors)

    assignment_result: dict[str, Any]
    try:
        assignment_result = anchor_today_clusters(
            date_str=date_str,
            today_clusters=today_clusters,
            weekly_anchors=weekly_anchors,
            gateway=AnchorGateway(gateway),
        )
    except Exception as exc:
        if isinstance(exc, AnchorGatewayError):
            _logger.warning(
                "anchor_llm_failed date=%s reason=%s prompt=%s raw_response=%r",
                date_str,
                exc,
                exc.prompt,
                exc.raw_response,
            )
        else:
            _logger.warning("anchor_llm_failed date=%s reason=%s", date_str, exc, exc_info=True)
        if recorder is not None:
            recorder.set_degraded("anchor_call_failed", kind=classify_llm_error(exc).value)
            recorder.mark_stage("anchor_call", "degraded", reason="anchor_call_failed", error_class=type(exc).__name__)
        assignment_result = {
            "assignments": {str(cluster.get("cluster_id") or ""): "unanchored" for cluster in today_clusters},
            "new_anchors": [],
        }
    else:
        if recorder is not None:
            recorder.mark_stage("anchor_call", "ok")

    updated_anchors, final_mapping = update_anchors_with_assignments(
        weekly_anchors,
        assignment_result,
        today_clusters,
        date_str,
    )
    save_weekly_anchors(week_str, updated_anchors)
    split_topics, unanchored = split_topics_and_unanchored(today_clusters, final_mapping, updated_anchors)
    cluster_narratives = {
        str(cluster.get("cluster_id") or ""): str(cluster.get("narrative_one_line") or "").strip()
        for cluster in today_clusters
        if str(cluster.get("cluster_id") or "").strip()
    }

    existing_anchor_slugs = {anchor.slug for anchor in weekly_anchors}
    topics: list[dict[str, Any]] = []
    for item in split_topics:
        if not isinstance(item, dict):
            continue
        anchor_slug = str(item.get("anchor") or "").strip()
        if not anchor_slug:
            continue
        state = "started_today" if anchor_slug not in existing_anchor_slugs else "continuing"
        refs = [str(v) for v in (item.get("events_ref") or []) if str(v).strip()]
        narrative = str(item.get("narrative") or "").strip()
        if not narrative and refs:
            narrative = "；".join(
                text
                for text in (cluster_narratives.get(ref, "").strip() for ref in refs)
                if text
            )
        existing_decisions = [str(v) for v in (item.get("decisions") or []) if str(v).strip()]
        existing_shipped = [str(v) for v in (item.get("shipped") or []) if str(v).strip()]
        inferred_decisions, inferred_shipped = _infer_decisions_shipped(narrative)
        decisions = _unique_keep_order([*existing_decisions, *inferred_decisions], limit=3)
        shipped = _unique_keep_order([*existing_shipped, *inferred_shipped], limit=3)
        topics.append(
            {
                "anchor": anchor_slug,
                "anchor_state": state,
                "narrative": narrative,
                "decisions": decisions,
                "shipped": shipped,
                "events_ref": refs,
                "display": str(item.get("anchor_display") or anchor_slug).strip() or anchor_slug,
            }
        )
    topics.sort(key=lambda item: str(item.get("anchor") or ""))

    summary_events: list[dict[str, Any]] = []
    for cluster in today_clusters:
        cluster_id = str(cluster.get("cluster_id") or "").strip()
        if not cluster_id:
            continue
        target = str(final_mapping.get(cluster_id) or "").strip() or None
        summary_events.append(
            {
                "cluster_id": cluster_id,
                "display_name": str(cluster.get("display_name") or cluster_id).strip(),
                "narrative_one_line": str(cluster.get("narrative_one_line") or "").strip(),
                "event_count": int(cluster.get("event_count") or 0),
                "time_range": list(cluster.get("time_range") or ["00:00", "23:59"]),
                "anchored_to": target,
                "merge_candidate_with": [str(v) for v in (cluster.get("merge_candidate_with") or []) if str(v).strip()],
                "peak_event_density": float(cluster.get("peak_event_density") or 0.0),
                "dwell_minutes": float(cluster.get("dwell_minutes") or 0.0),
                "revisit_count": int(cluster.get("revisit_count") or 0),
                "cross_app_count": int(cluster.get("cross_app_count") or 0),
            }
        )
    summary_events.sort(
        key=lambda item: (
            -float(item.get("peak_event_density") or 0.0),
            str((item.get("time_range") or ["00:00"])[0]),
            str(item.get("display_name") or ""),
        )
    )

    unanchored_events: list[dict[str, Any]] = []
    for item in unanchored:
        if not isinstance(item, dict):
            continue
        unanchored_events.append(
            {
                "cluster_id": str(item.get("cluster_id") or "").strip(),
                "display_name": str(item.get("display_name") or "").strip(),
                "narrative_one_line": str(item.get("narrative_one_line") or "").strip(),
                "event_count": int(item.get("event_count") or 0),
                "time_range": list(item.get("time_range") or ["00:00", "23:59"]),
                "anchored_to": None,
                "merge_candidate_with": [str(v) for v in (item.get("merge_candidate_with") or []) if str(v).strip()],
                "peak_event_density": float(item.get("peak_event_density") or 0.0),
                "dwell_minutes": float(item.get("dwell_minutes") or 0.0),
                "revisit_count": int(item.get("revisit_count") or 0),
                "cross_app_count": int(item.get("cross_app_count") or 0),
            }
        )
    return topics, summary_events, unanchored_events


def run_daily(date_str: str, *, trigger: str = "18:00") -> DailySummary:
    if trigger not in _TRIGGER_VALUES:
        raise ValueError(f"invalid trigger: {trigger}")
    with RunRecorder(date_str=date_str, kind="daily", trigger=trigger) as recorder:
        return _run_daily_recorded(date_str, trigger=trigger, recorder=recorder)


def _run_daily_recorded(date_str: str, *, trigger: str, recorder: RunRecorder) -> DailySummary:
    run_started_at = datetime.now(timezone.utc)
    with recorder.stage("load_rows"):
        rows = _load_rows_for_date(date_str)
        recorder.set_core_watcher_emit_counts(_core_watcher_emit_counts_for_date(date_str, rows))
        scoped_rows = _filter_for_trigger(date_str, trigger, rows)
        events = [_extract_event_payload(row) for row in scoped_rows]
        recorder.set_input_count(len(events))

    if len(events) < 3:
        recorder.set_output_quality("degraded", degraded_reason="low_event_count")
        with recorder.stage("render"):
            daily_path = _daily_path(date_str)
            body = render_daily_markdown(date=date_str, topics=[], events=[], topic_snapshot={})
        with recorder.stage("persist"):
            write_artifact(recorder, daily_path, body, stage="persist_daily_markdown")
            cost = _cost_snapshot(run_started_at)
            summary_path = write_daily_summary(
                date_str,
                clusters=_ensure_summary_clusters(date_str, body, events),
                misc=[str(event.get("id")) for event in events],
                topic_snapshot={},
                cost=cost,
                topics=[],
                unanchored=[],
                recorder=recorder,
                stage="persist_daily_summary",
            )
            recorder.set_cost(cost)
        _append_log(
            {
                "ts": _now_iso(),
                "capability": "daily_orchestrator",
                "date": date_str,
                "trigger": trigger,
                "decision": "skip_low_volume",
                "event_count": len(events),
            }
        )
        _maybe_trigger_weekly_after_daily(date_str)
        return DailySummary(
            date=date_str,
            trigger=trigger,
            event_count=len(rows),
            processed_count=len(events),
            cluster_count=0,
            misc_event_ids=tuple(str(event.get("id")) for event in events),
            topic_diffs=tuple(),
            daily_path=str(daily_path),
            summary_path=str(summary_path),
            skipped=True,
        )

    gateway = _load_gateway()
    tier = _resolve_daily_tier(gateway)
    density_settings = Config.load().pipeline.value_density

    if tier == "flagship":
        strategy = FlagshipSingleStepStrategy(
            cluster_components=lambda scoped_events: _cluster_component_payloads(scoped_events, density_settings)[0]
        )
        with recorder.stage("cap_events"):
            flagship_events, events_capped = _cap_flagship_events_for_prompt(events)
            recorder.set_input_count(len(flagship_events))
        if events_capped:
            _logger.warning(
                "daily_orchestrator events_capped count=%s capped=%s reason=token_guard",
                len(events),
                len(flagship_events),
            )
            _append_log(
                {
                    "ts": _now_iso(),
                    "capability": "daily_orchestrator",
                    "date": date_str,
                    "trigger": trigger,
                    "decision": "events_capped",
                    "reason": "token_guard",
                    "count": len(events),
                    "capped": len(flagship_events),
                    "event_count": len(events),
                    "capped_count": len(flagship_events),
                }
            )
        try:
            with recorder.stage("flagship_call", failure_reason="flagship_failed"):
                result = strategy.generate(date_str=date_str, events=flagship_events, gateway=gateway)
        except DailyStrategyError as exc:
            llm_exc = exc.__cause__ if isinstance(exc.__cause__, BaseException) else exc
            recorder.set_degraded("flagship_failed", kind=classify_llm_error(llm_exc).value)
            raise DailyOrchestratorError(str(exc)) from exc

        before_things = _count_h3_headings(result.markdown)
        repair_triggered = before_things < 3
        repair_things: int | None = None
        repair_failure_reason = ""
        if repair_triggered:
            component_count = len(_cluster_component_payloads(flagship_events, density_settings)[0])
            repair_hint = _build_flagship_repair_hint(
                before_things=before_things,
                component_count=component_count,
                minimum_things=3,
            )
            try:
                with recorder.stage("flagship_repair_call", failure_reason="flagship_repair_failed"):
                    repaired_result = strategy.generate(
                        date_str=date_str,
                        events=flagship_events,
                        gateway=gateway,
                        repair_hint=repair_hint,
                    )
            except DailyStrategyError as exc:
                llm_exc = exc.__cause__ if isinstance(exc.__cause__, BaseException) else exc
                recorder.set_degraded("flagship_repair_failed", kind=classify_llm_error(llm_exc).value)
                recorder.mark_stage(
                    "flagship_repair_call",
                    "degraded",
                    reason="flagship_repair_failed",
                    error_class=type(llm_exc).__name__,
                )
                repair_failure_reason = f"{type(llm_exc).__name__}:{llm_exc}"
            else:
                repair_things = _count_h3_headings(repaired_result.markdown)
                result = repaired_result
                if repair_things < 3:
                    recorder.set_output_quality("degraded", degraded_reason="flagship_repair_things_lt_3")
                    recorder.mark_stage(
                        "flagship_repair_call",
                        "degraded",
                        reason="flagship_repair_things_lt_3",
                    )
                    repair_failure_reason = f"repair_things_lt_3:{repair_things}"

        final_things = _count_h3_headings(result.markdown)
        if repair_triggered:
            _append_log(
                {
                    "ts": _now_iso(),
                    "capability": "daily_orchestrator",
                    "date": date_str,
                    "trigger": trigger,
                    "decision": "flagship_repair",
                    "reason": "things_lt_3",
                    "before_things": before_things,
                    "repair_things": repair_things,
                    "final_things": final_things,
                    "failure_reason": repair_failure_reason,
                    "status": "degraded" if repair_failure_reason else "ok",
                }
            )

        summary_clusters = _ensure_summary_clusters(date_str, result.markdown, events)
        today_clusters = _summary_clusters_to_anchor_clusters(summary_clusters)
        topics, summary_events, unanchored_events = _run_anchor_for_clusters(
            date_str=date_str,
            today_clusters=today_clusters,
            gateway=gateway,
            recorder=recorder,
        )
        topic_snapshot = build_topic_status_snapshot_from_narrative(date_str, result.markdown)
        with recorder.stage("render"):
            daily_markdown = render_daily_markdown(
                date=date_str,
                topics=topics,
                events=summary_events,
                unanchored=unanchored_events,
                narrative_markdown=result.markdown,
                topic_snapshot=topic_snapshot,
                model_gateway=gateway,
            )
            daily_path = _daily_path(date_str)
        _append_log(
            {
                "ts": _now_iso(),
                "capability": "daily_orchestrator",
                "date": date_str,
                "trigger": trigger,
                "tier": "flagship",
                "strategy": strategy.name,
            }
        )
        with recorder.stage("persist"):
            write_artifact(recorder, daily_path, daily_markdown, stage="persist_daily_markdown")
            cost = _cost_snapshot(run_started_at)
            summary_path = write_daily_summary(
                date_str,
                clusters=summary_clusters,
                misc=[str(event.get("cluster_id") or "") for event in unanchored_events if str(event.get("cluster_id") or "").strip()],
                topic_snapshot=topic_snapshot,
                cost=cost,
                topics=topics,
                events=summary_events,
                unanchored=unanchored_events,
                narrative_markdown=result.markdown,
                recorder=recorder,
                stage="persist_daily_summary",
            )
            recorder.set_cost(cost)
        if recorder.output_quality != "degraded":
            recorder.set_output_quality("ok")
        _maybe_trigger_weekly_after_daily(date_str)
        return DailySummary(
            date=date_str,
            trigger=trigger,
            event_count=len(rows),
            processed_count=len(events),
            cluster_count=0,
            misc_event_ids=tuple(),
            topic_diffs=tuple(),
            daily_path=str(daily_path),
            summary_path=str(summary_path),
            skipped=False,
        )

    recorder.mark_stage("cap_events", "ok")
    merge_cache: dict[str, Any] = {"pairs": []}

    def cluster_components(scoped_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        payloads, merge_pairs = _cluster_component_payloads(scoped_events, density_settings)
        merge_cache["pairs"] = merge_pairs
        return payloads

    deps = BudgetStrategyDeps(
        cluster_components=cluster_components,
        load_topics_index=_load_topic_index,
        load_hot_slugs=_load_hot_slugs,
        prune_topics=_prune_topics,
        topic_display_name=_topic_display_name,
        detect_merges=lambda _component_payloads: list(merge_cache.get("pairs") or []),
    )
    strategy = BudgetTwoStepStrategy(deps)
    try:
        with recorder.stage("flagship_call", failure_reason="flagship_failed"):
            result = strategy.generate(date_str=date_str, events=events, gateway=gateway)
    except DailyStrategyError as exc:
        llm_exc = exc.__cause__ if isinstance(exc.__cause__, BaseException) else exc
        recorder.set_degraded("flagship_failed", kind=classify_llm_error(llm_exc).value)
        raise DailyOrchestratorError(str(exc)) from exc

    cluster_records = list(result.clusters)
    events_by_id = {str(event.get("id")): event for event in events}
    new_cluster_inputs: list[tuple[ClusterRecord, list[dict[str, Any]]]] = []
    for cluster in cluster_records:
        if cluster.topic_action != "new":
            continue
        component_events = [events_by_id[event_id] for event_id in cluster.event_ids if event_id in events_by_id]
        new_cluster_inputs.append((cluster, component_events))

    l3_results: dict[str, dict[str, Any]] = {}
    topics_index = _load_topic_index()
    existing_slugs = {str(item.get("slug")) for item in topics_index if str(item.get("slug") or "").strip()}
    if new_cluster_inputs:
        try:
            from keypulse.prompts.loader import load_prompt

            l3_spec = load_prompt("L3_topic_naming")
            with ThreadPoolExecutor(max_workers=min(8, len(new_cluster_inputs))) as executor:
                futures = {}
                for cluster, component_events in new_cluster_inputs:
                    payload = {
                        "trigger": trigger,
                        "component_id": cluster.component_id,
                        "events": [
                            {
                                "id": str(event.get("id")),
                                "content": str(event.get("content_text") or ""),
                                "app": str(event.get("app_name") or ""),
                                "timestamp": _to_local_mmdd_hhmm(str(event.get("ts_start") or "")),
                            }
                            for event in component_events
                        ],
                        "existing_slugs": sorted(existing_slugs),
                    }
                    prompt = _build_prompt(l3_spec.body, "L3_topic_naming", payload)
                    futures[
                        executor.submit(
                            gateway.call,
                            "L3_topic_naming",
                            prompt,
                            input_data=payload,
                        )
                    ] = cluster.component_id
                for future in as_completed(futures):
                    component_id = futures[future]
                    try:
                        named = future.result()
                    except (LLMCallError, ValueError, KeyError, OSError, RuntimeError):
                        continue
                    if isinstance(named, dict):
                        l3_results[component_id] = named
                        slug = str(named.get("slug") or "").strip()
                        if slug:
                            existing_slugs.add(slug)
        except (LLMCallError, ValueError, KeyError, OSError, RuntimeError):
            l3_results = {}

    topics_touched: list[tuple[str, str]] = []
    topic_diffs: list[str] = []
    resolved_clusters: list[ClusterRecord] = []
    for cluster in cluster_records:
        resolved = cluster
        if cluster.topic_action == "new":
            named = l3_results.get(cluster.component_id, {})
            candidate_slug = str(named.get("slug") or "").strip()
            if candidate_slug and _SLUG_RE.fullmatch(candidate_slug):
                topic_slug = candidate_slug
            else:
                fallback_text = " ".join(
                    str(events_by_id[event_id].get("content_text") or "")
                    for event_id in cluster.event_ids
                    if event_id in events_by_id
                )
                topic_slug = _slugify_topic(
                    fallback_text or cluster.display_name,
                    fallback=f"topic-{date_str.replace('-', '')}-{cluster.component_id.lower()}",
                )
            display_name = str(named.get("display_name") or topic_slug).strip() or topic_slug
            resolved = replace(cluster, topic_slug=topic_slug, display_name=display_name)

        narrative = _extract_narrative_one_line(result.markdown, resolved.display_name)
        if not narrative:
            component_events = [events_by_id[event_id] for event_id in resolved.event_ids if event_id in events_by_id]
            start, end = _component_time_range(component_events)
            narrative = f"本主题共 {len(resolved.event_ids)} 条相关事件，时间范围 {start}-{end}。"
        resolved = replace(resolved, narrative_one_line=narrative)

        keywords = list(resolved.keywords)
        if resolved.topic_action == "new":
            keywords = [str(item) for item in (l3_results.get(resolved.component_id, {}).get("keywords") or [])] or keywords
        else:
            for item in topics_index:
                if str(item.get("slug") or "") == resolved.topic_slug:
                    keywords = [str(v) for v in (item.get("keywords") or [])]
                    break

        diff = _upsert_topic(
            date_str=date_str,
            trigger=trigger,
            slug=resolved.topic_slug,
            display_name=resolved.display_name,
            keywords=keywords,
            narrative=narrative,
            event_ids=list(resolved.event_ids),
        )
        topic_diffs.append(f"{resolved.topic_slug}:{diff}")
        topics_touched.append((resolved.topic_slug, resolved.display_name))
        resolved_clusters.append(resolved)

    _refresh_hot(topics_touched, date_str)
    _append_log(
        {
            "ts": _now_iso(),
            "capability": "daily_orchestrator",
            "date": date_str,
            "trigger": trigger,
            "tier": "budget",
            "strategy": strategy.name,
            "cluster_count": len(resolved_clusters),
            "misc_count": len(result.misc_event_ids),
            "topic_diffs": topic_diffs,
            "merge_candidates": list(result.merge_candidates),
        }
    )

    merge_map: dict[str, list[str]] = {}
    for left, right in result.merge_candidates:
        merge_map.setdefault(left, []).append(right)
        merge_map.setdefault(right, []).append(left)

    summary_clusters: list[dict[str, Any]] = []
    today_clusters: list[dict[str, Any]] = []
    for cluster in resolved_clusters:
        component_events = [events_by_id[event_id] for event_id in cluster.event_ids if event_id in events_by_id]
        start, end = _component_time_range(component_events)
        summary_clusters.append(
            {
                "slug": cluster.topic_slug,
                "display_name": cluster.display_name,
                "narrative_one_line": cluster.narrative_one_line,
                "event_count": len(cluster.event_ids),
                "time_range": [start, end],
                "merge_candidate_with": merge_map.get(cluster.component_id, []),
                "peak_event_density": cluster.peak_event_density,
                "dwell_minutes": cluster.dwell_minutes,
                "revisit_count": cluster.revisit_count,
                "cross_app_count": cluster.cross_app_count,
            }
        )
        today_clusters.append(
            {
                "cluster_id": cluster.component_id,
                "display_name": cluster.display_name,
                "narrative_one_line": cluster.narrative_one_line,
                "event_count": len(cluster.event_ids),
                "time_range": [start, end],
                "merge_candidate_with": merge_map.get(cluster.component_id, []),
                "peak_event_density": cluster.peak_event_density,
                "dwell_minutes": cluster.dwell_minutes,
                "revisit_count": cluster.revisit_count,
                "cross_app_count": cluster.cross_app_count,
            }
        )

    topics, summary_events, unanchored_events = _run_anchor_for_clusters(
        date_str=date_str,
        today_clusters=today_clusters,
        gateway=gateway,
        recorder=recorder,
    )

    topic_snapshot = build_topic_status_snapshot_from_narrative(date_str, result.markdown) or {
        slug: {"name": display, "state": "in_progress", "last_seen_date": date_str, "evidence_dates": [date_str]}
        for slug, display in topics_touched
    }

    with recorder.stage("render"):
        daily_markdown = render_daily_markdown(
            date=date_str,
            topics=topics,
            events=summary_events,
            unanchored=unanchored_events,
            narrative_markdown=result.markdown,
            topic_snapshot=topic_snapshot,
            model_gateway=gateway,
        )
        daily_path = _daily_path(date_str)

    with recorder.stage("persist"):
        write_artifact(recorder, daily_path, daily_markdown, stage="persist_daily_markdown")
        cost = _cost_snapshot(run_started_at)
        summary_path = write_daily_summary(
            date_str,
            clusters=summary_clusters,
            misc=[str(event.get("cluster_id") or "") for event in unanchored_events if str(event.get("cluster_id") or "").strip()],
            topic_snapshot=topic_snapshot,
            cost=cost,
            topics=topics,
            events=summary_events,
            unanchored=unanchored_events,
            narrative_markdown=result.markdown,
            recorder=recorder,
            stage="persist_daily_summary",
        )
        recorder.set_cost(cost)
    if recorder.output_quality != "degraded":
        recorder.set_output_quality("ok")

    _maybe_trigger_weekly_after_daily(date_str)
    return DailySummary(
        date=date_str,
        trigger=trigger,
        event_count=len(rows),
        processed_count=len(events),
        cluster_count=len(resolved_clusters),
        misc_event_ids=tuple(result.misc_event_ids),
        topic_diffs=tuple(topic_diffs),
        daily_path=str(daily_path),
        summary_path=str(summary_path),
        skipped=False,
    )
