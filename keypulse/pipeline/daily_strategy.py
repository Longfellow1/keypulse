"""Daily generation strategies.

Two paths, picked at runtime by `model_card.resolve_tier()`:

- **FlagshipSingleStepStrategy** (tier="flagship"): one big-model call eats
  the whole day's events and emits a finished daily.md. Skips clustering,
  L1, L3, and topics-index maintenance — large models cluster + narrate
  in their head. Cheaper attention budget; no topics side-effects.

- **BudgetTwoStepStrategy** (tier="budget"): hard-cluster (rule-based) →
  L1 LLM cluster review → L2 LLM narrative (single call covering the
  whole day). Maintains topics index and hot.md the way budget models
  benefit from a structured handoff.

The orchestrator owns IO (writing daily.md, log.md, daily summary),
clustering helpers, and topic-index updates. Strategies only know how
to turn events into markdown + the structural metadata the orchestrator
needs to update side state.
"""
from __future__ import annotations

import abc
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from keypulse.i18n import current_lang
from keypulse.pipeline.model import LLMCallError, ModelGateway
from keypulse.utils.dates import local_timezone


_INPUT_MARKER_BEGIN = "<<INPUT_JSON>>"
_INPUT_MARKER_END = "<<END_INPUT_JSON>>"


class DailyStrategyError(RuntimeError):
    """Raised when a strategy cannot complete; orchestrator translates this
    to DailyOrchestratorError so callers see a uniform failure type."""


@dataclass(frozen=True)
class ClusterRecord:
    """Lightweight cluster summary for the orchestrator to update topics/hot/summary.

    Flagship strategy returns an empty tuple; budget returns one per non-misc cluster.
    """
    component_id: str
    topic_action: str  # "existing" | "new"
    topic_slug: str
    display_name: str
    event_ids: tuple[str, ...]
    keywords: tuple[str, ...]
    narrative_one_line: str  # for daily-summary index, NOT the full daily.md text
    peak_event_density: float = 0.0
    dwell_minutes: float = 0.0
    revisit_count: int = 0
    cross_app_count: int = 0


@dataclass(frozen=True)
class DailyGenerationResult:
    """Strategy output. Orchestrator handles all writes; strategy only computes."""
    markdown: str
    misc_event_ids: tuple[str, ...] = ()
    clusters: tuple[ClusterRecord, ...] = ()
    merge_candidates: tuple[tuple[str, str], ...] = ()


def build_prompt(spec_body: str, capability: str, payload: Mapping[str, Any]) -> str:
    """Build the same `CAPABILITY: ... <<INPUT_JSON>> ...` envelope the orchestrator uses."""
    rendered_spec = spec_body.replace("{{lang}}", current_lang()).strip()
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    return "\n".join(
        [
            f"CAPABILITY: {capability}",
            rendered_spec,
            _INPUT_MARKER_BEGIN,
            rendered,
            _INPUT_MARKER_END,
        ]
    )


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


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


_WIN_TITLE_SEP_RE = re.compile(r"\s+[—–-]\s+")
_DASH_ENCODED_PATH_RE = re.compile(r"^-Users-[A-Za-z0-9]+-")
_DASH_ENCODED_LEAF_RE = re.compile(r"([A-Z][A-Z0-9]+-\d[\w-]*)$")


def _decode_dash_path(value: str) -> str:
    text = str(value or "").strip()
    if _DASH_ENCODED_PATH_RE.match(text):
        return "/" + text.lstrip("-").replace("-", "/")
    return text


def _basename_no_ext(path: str) -> str:
    name = str(path or "").rstrip("/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name


def _work_unit_from_file_path(path: str) -> str:
    parts = [part for part in re.split(r"[\\/]+", str(path or "")) if part]
    for part in parts:
        if _DASH_ENCODED_PATH_RE.match(part):
            leaf = _DASH_ENCODED_LEAF_RE.search(part)
            if leaf:
                return leaf.group(1)
            return _basename_no_ext(_decode_dash_path(part))
    return _basename_no_ext(path)


def extract_work_unit(event: Mapping[str, Any]) -> str:
    """Identify the user's active document, conversation, page, or app."""
    window_title = str(event.get("window_title") or "").strip()
    if window_title:
        parts = _WIN_TITLE_SEP_RE.split(window_title)
        if len(parts) >= 2:
            title = parts[0].strip()
            if title and len(title) <= 80:
                return title
        elif len(window_title) <= 80:
            return window_title

    metadata = _metadata_dict(event)
    code_symbols = _list_of_strings(metadata.get("code_symbols"))
    if code_symbols:
        return code_symbols[0]

    entities = _metadata_entities(event)
    file_paths = _list_of_strings(entities.get("file_paths"))
    if file_paths:
        work_unit = _work_unit_from_file_path(file_paths[0])
        if work_unit:
            return work_unit

    urls = _list_of_strings(entities.get("urls"))
    url = urls[0] if urls else str(metadata.get("url") or "").strip()
    if url:
        from urllib.parse import urlsplit

        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or parsed.netloc or "").strip().lower()
            if host:
                segment = next((part for part in (parsed.path or "/").strip("/").split("/") if part), "")
                return f"{host}/{segment}" if segment else host
        except ValueError:
            pass

    app_name = str(event.get("app_name") or "").strip()
    if app_name and app_name.lower() != "unknown":
        return app_name

    content = str(event.get("content_text") or "").strip()
    if content:
        snippet = re.sub(r"\s+", " ", content)[:20].strip()
        if snippet:
            return snippet

    source = str(event.get("source") or "").strip()
    return source or "unknown"


def _flagship_cluster_payload(component: Mapping[str, Any]) -> dict[str, Any]:
    event_ids = [str(item) for item in (component.get("event_ids") or []) if str(item).strip()]
    payload: dict[str, Any] = {
        "display_name": str(component.get("display_name") or component.get("component_id") or "").strip(),
        "dwell_minutes": _payload_float(component, "dwell_minutes", 0.0),
        "revisit_count": int(_payload_float(component, "revisit_count", 0.0)),
        "cross_app_count": int(_payload_float(component, "cross_app_count", 0.0)),
        "event_count": int(component.get("event_count") or len(event_ids)),
        "time_range": list(component.get("time_range") or []),
        "key_excerpts": [
            str(item)[:80]
            for item in (component.get("key_excerpts") or [])
            if str(item).strip()
        ][:3],
    }
    return {key: value for key, value in payload.items() if value not in ("", [], None)}


def to_compact_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Orchestrator payload → flagship/budget L2 compact form `{t, s, a, c, sp}`.

    Truncates content to 320 chars — keeps the
    full-day prompt within tokens budget while preserving signal density.
    """
    ts = str(event.get("ts_start") or "")
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        hhmm = parsed.astimezone(local_timezone()).strftime("%H:%M")
    except ValueError:
        hhmm = ts[11:16] if len(ts) >= 16 and ts[10] == "T" else ts[:5]
    out: dict[str, Any] = {
        "t": hhmm,
        "s": str(event.get("source") or "ax_text"),
        "c": (str(event.get("content_text") or "").strip())[:320],
    }
    event_id = str(event.get("id") or event.get("event_id") or "").strip()
    if event_id:
        out["eid"] = event_id
    entities = _metadata_entities(event)
    session_id = str(event.get("session_id") or entities.get("session_id") or "").strip()
    if session_id:
        out["sid"] = session_id
    window_title = str(event.get("window_title") or "").strip()
    if window_title:
        out["win"] = window_title[:80]
    work_unit = extract_work_unit(event)
    if work_unit and work_unit != "unknown":
        out["wu"] = work_unit
    file_paths = _list_of_strings(entities.get("file_paths"))
    if file_paths:
        out["fp"] = [path[:60] for path in file_paths[:3]]
    urls = _list_of_strings(entities.get("urls"))
    if urls:
        out["url"] = urls[0]
    code_symbols = _list_of_strings(_metadata_dict(event).get("code_symbols"))
    if code_symbols:
        out["code_symbols"] = code_symbols[:5]
    app = str(event.get("app_name") or "").strip()
    if app:
        out["a"] = app
    speaker = str(event.get("speaker") or "").strip()
    if speaker:
        out["sp"] = speaker
    return out


def _daily_dir_from_config() -> Path:
    """Resolve the configured Obsidian Daily directory."""
    from keypulse.config import Config

    cfg = Config.load()
    vault = Path(cfg.obsidian.vault_path).expanduser()
    return vault / "Daily"


def _load_yesterday_anchor(date_str: str) -> str:
    """Load yesterday's manually filled tomorrow anchor."""
    try:
        yesterday = (date.fromisoformat(date_str) - timedelta(days=1)).isoformat()
        daily_path = _daily_dir_from_config() / f"{yesterday}.md"
        if not daily_path.exists():
            return ""
        text = daily_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""

    heading = re.search(r"^## 明日的锚点\s*$", text, re.MULTILINE)
    if heading is None:
        return ""
    next_heading = re.search(r"^## ", text[heading.end() :], re.MULTILINE)
    section_end = heading.end() + next_heading.start() if next_heading else len(text)
    section = text[heading.end() : section_end]

    lines: list[str] = []
    for raw_line in section.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped == ">":
            continue
        if stripped.startswith("> 明天我想：") or stripped.startswith("> _写一句话"):
            continue
        cleaned = re.sub(r"^>\s?", "", stripped).strip()
        if cleaned:
            lines.append(cleaned)
    return " ".join(lines).strip()


def _build_recent_topic_history(date_str: str, days: int = 7) -> list[dict[str, Any]]:
    """Build recent topic history from prior Daily H3 anchors."""
    try:
        current_date = date.fromisoformat(date_str)
        daily_dir = _daily_dir_from_config()
    except (OSError, ValueError):
        return []

    topics: dict[str, dict[str, Any]] = {}
    for offset in range(days, 0, -1):
        active_date = current_date - timedelta(days=offset)
        active_date_str = active_date.isoformat()
        daily_path = daily_dir / f"{active_date_str}.md"
        try:
            if not daily_path.exists():
                continue
            text = daily_path.read_text(encoding="utf-8")
        except OSError:
            continue

        matches = list(re.finditer(r"^### \[\[(?P<anchor>[^|\]]+)\|(?P<display>[^\]]+)\]\]", text, re.MULTILINE))
        for match in matches:
            anchor = match.group("anchor").strip()
            display = match.group("display").strip()
            body_start = match.end()
            next_match = re.search(r"^(?:### |## )", text[body_start:], re.MULTILINE)
            body_end = body_start + next_match.start() if next_match else len(text)
            summary = _topic_history_summary(text[body_start:body_end])
            record = topics.setdefault(
                anchor,
                {
                    "anchor": anchor,
                    "display": display,
                    "active_dates": [],
                    "last_status": "new",
                    "last_summary": "",
                },
            )
            record["display"] = display
            record["active_dates"] = [*record["active_dates"], active_date_str]
            record["last_summary"] = summary

    for record in topics.values():
        active_dates = record["active_dates"]
        record["last_status"] = _topic_history_status(active_dates)

    return sorted(topics.values(), key=lambda item: item["active_dates"][-1], reverse=True)


def _topic_history_summary(markdown: str) -> str:
    """Extract a compact summary from a topic section body."""
    plain = re.sub(r"^\s*[-*>#]+\s*", "", markdown, flags=re.MULTILINE)
    plain = plain.strip()
    if not plain:
        return ""
    parts = [part.strip() for part in re.split(r"[。.!?！？\n]+", plain) if part.strip()]
    summary = " ".join(parts[:2]).strip() if parts else plain
    return summary[:80].strip()


def _topic_history_status(active_dates: list[str]) -> str:
    """Classify recent topic continuity."""
    if len(active_dates) <= 1:
        return "new"
    parsed_dates = [date.fromisoformat(item) for item in active_dates]
    current_chain = 1
    max_chain = 1
    for previous, current in zip(parsed_dates, parsed_dates[1:]):
        if (current - previous).days <= 1:
            current_chain += 1
            max_chain = max(max_chain, current_chain)
        else:
            current_chain = 1
    if max_chain >= 2 and max_chain / len(parsed_dates) >= 0.5:
        return "active"
    return "reopens"


def _payload_float(payload: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _payload_time_start(payload: Mapping[str, Any]) -> str:
    time_range = payload.get("time_range") or []
    if isinstance(time_range, list) and time_range:
        return str(time_range[0])
    return ""


class DailyStrategy(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    def generate(
        self,
        *,
        date_str: str,
        events: list[dict[str, Any]],
        gateway: ModelGateway,
        repair_hint: str = "",
    ) -> DailyGenerationResult:
        """Turn full-day events into a daily.md markdown + structural metadata.

        Implementations may raise DailyStrategyError on unrecoverable failure.
        """
        ...


class FlagshipSingleStepStrategy(DailyStrategy):
    """One large-model call → finished daily.md. No clustering / no topics."""

    name = "flagship"
    capability = "daily_flagship"

    def __init__(self, cluster_components: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None) -> None:
        self._cluster_components = cluster_components

    def generate(
        self,
        *,
        date_str: str,
        events: list[dict[str, Any]],
        gateway: ModelGateway,
        repair_hint: str = "",
    ) -> DailyGenerationResult:
        from keypulse.prompts.loader import load_prompt

        compact = [to_compact_event(event) for event in events]
        payload: dict[str, Any] = {
            "date": date_str,
            "events": compact,
            "yesterday_anchor": _load_yesterday_anchor(date_str),
            "recent_topic_history": _build_recent_topic_history(date_str, days=7),
        }
        if self._cluster_components is not None:
            clusters = [_flagship_cluster_payload(component) for component in self._cluster_components(events)]
            payload["clusters"] = [cluster for cluster in clusters if cluster]

        try:
            spec = load_prompt(self.capability)
            prompt_body = spec.body
            hint = str(repair_hint or "").strip()
            if hint:
                prompt_body = "\n".join(["REPAIR MODE", hint, "", prompt_body])
            prompt = build_prompt(prompt_body, self.capability, payload)
            output = gateway.call(self.capability, prompt, input_data=payload)
        except (LLMCallError, ValueError, KeyError, OSError) as exc:
            raise DailyStrategyError(f"flagship LLM call failed: {exc}") from exc

        if isinstance(output, dict):
            markdown = str(output.get("markdown") or "").strip()
        else:
            markdown = str(output).strip()
        if not markdown:
            raise DailyStrategyError("flagship strategy returned empty markdown")

        return DailyGenerationResult(markdown=markdown)


@dataclass
class BudgetStrategyDeps:
    """Dependencies budget strategy needs from orchestrator. Passed in to keep
    daily_strategy.py decoupled from clustering / topic-index implementation
    details (those live in daily_orchestrator + clustering modules)."""
    cluster_components: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    """events → list of {component_id, event_ids, keywords, entities, time_range}"""

    load_topics_index: Callable[[], list[dict[str, Any]]]
    load_hot_slugs: Callable[[], list[str]]
    prune_topics: Callable[[list[dict[str, Any]], list[str], list[dict[str, Any]]], list[dict[str, Any]]]
    topic_display_name: Callable[[str, list[dict[str, Any]]], str]
    detect_merges: Callable[[list[dict[str, Any]]], list[tuple[str, str]]]


class BudgetTwoStepStrategy(DailyStrategy):
    """Two budget-model calls: L1 cluster review (existing) + L2 narrative
    (new — one shot for the whole day, replaces per-cluster N calls).

    L3 topic-naming is invoked on demand for "new" clusters by the
    orchestrator after this strategy returns — it's a topic-management
    side effect, not part of narrative generation."""

    name = "budget"

    def __init__(self, deps: BudgetStrategyDeps) -> None:
        self._deps = deps

    def generate(
        self,
        *,
        date_str: str,
        events: list[dict[str, Any]],
        gateway: ModelGateway,
        repair_hint: str = "",
    ) -> DailyGenerationResult:
        from keypulse.prompts.loader import load_prompt
        del repair_hint

        component_payloads = self._deps.cluster_components(events)
        merge_candidates = self._deps.detect_merges(component_payloads)
        topics_index = self._deps.load_topics_index()
        hot_slugs = self._deps.load_hot_slugs()
        pruned_topics = self._deps.prune_topics(topics_index, hot_slugs, events)

        l1_input = {
            "scope_date": date_str,
            "trigger": "all-day",
            "components": component_payloads,
            "merge_candidates": [list(pair) for pair in merge_candidates],
            "existing_topics_index": pruned_topics,
            "hot_cache": hot_slugs,
            "hud_input_today": None,
        }

        try:
            l1_spec = load_prompt("L1_cluster_review")
            l1_prompt = build_prompt(l1_spec.body, "L1_cluster_review", l1_input)
            l1_output = gateway.call("L1_cluster_review", l1_prompt, input_data=l1_input)
        except (LLMCallError, ValueError, KeyError, OSError) as exc:
            raise DailyStrategyError(f"L1 cluster review failed: {exc}") from exc

        if not isinstance(l1_output, dict):
            raise DailyStrategyError("L1 output must be object")

        decisions: dict[str, dict[str, Any]] = {}
        for item in l1_output.get("clusters", []) or []:
            if isinstance(item, dict):
                cid = str(item.get("component_id") or "").strip()
                if cid:
                    decisions[cid] = item

        misc_ids: list[str] = [str(v) for v in (l1_output.get("misc_event_ids") or []) if str(v).strip()]
        valid_event_ids = {str(event.get("id")) for event in events}
        misc_ids = [eid for eid in misc_ids if eid in valid_event_ids]

        cluster_records: list[ClusterRecord] = []
        l2_clusters: list[dict[str, Any]] = []
        misc_events_compact: list[dict[str, Any]] = []

        events_by_id = {str(event.get("id")): event for event in events}
        component_payloads_by_id = {str(payload.get("component_id") or ""): payload for payload in component_payloads}

        for component_payload in component_payloads:
            component_id = str(component_payload["component_id"])
            event_ids: list[str] = list(component_payload["event_ids"])
            decision = decisions.get(component_id, {"topic_action": "misc"})
            action = str(decision.get("topic_action") or "misc")
            peak_event_density = _payload_float(component_payload, "peak_event_density", 0.0)

            slug = str(decision.get("topic_slug") or "").strip() or None
            if action == "existing" and not slug:
                action = "misc"

            if action == "misc":
                for eid in event_ids:
                    if eid not in misc_ids:
                        misc_ids.append(eid)
                continue

            display_name = (
                self._deps.topic_display_name(slug, pruned_topics)
                if (slug and action == "existing")
                else f"主题-{component_id}"
            )
            keywords = tuple(component_payload.get("keywords") or [])
            cluster_events_compact = [
                to_compact_event(events_by_id[eid])
                for eid in event_ids
                if eid in events_by_id
            ]

            cluster_records.append(
                ClusterRecord(
                    component_id=component_id,
                    topic_action=action,
                    topic_slug=slug or "",
                    display_name=display_name,
                    event_ids=tuple(event_ids),
                    keywords=keywords,
                    narrative_one_line="",  # populated by orchestrator from L2 output
                    peak_event_density=peak_event_density,
                    dwell_minutes=_payload_float(component_payload, "dwell_minutes", 0.0),
                    revisit_count=int(_payload_float(component_payload, "revisit_count", 0.0)),
                    cross_app_count=int(_payload_float(component_payload, "cross_app_count", 0.0)),
                )
            )
            l2_clusters.append(
                {
                    "component_id": component_id,
                    "display_name": display_name,
                    "topic_action": action,
                    "peak_event_density": peak_event_density,
                    "dwell_minutes": _payload_float(component_payload, "dwell_minutes", 0.0),
                    "revisit_count": int(_payload_float(component_payload, "revisit_count", 0.0)),
                    "cross_app_count": int(_payload_float(component_payload, "cross_app_count", 0.0)),
                    "key_excerpts": [
                        str(item)[:80]
                        for item in (component_payload.get("key_excerpts") or [])
                        if str(item).strip()
                    ][:3],
                    "events": cluster_events_compact,
                }
            )

        for eid in misc_ids:
            if eid in events_by_id:
                misc_events_compact.append(to_compact_event(events_by_id[eid]))

        l2_clusters.sort(
            key=lambda item: (
                -float(item.get("peak_event_density") or 0.0),
                _payload_time_start(component_payloads_by_id.get(str(item.get("component_id") or ""), {})),
                str(item.get("display_name") or ""),
            )
        )

        l2_input = {
            "date": date_str,
            "clusters": l2_clusters,
            "misc_events": misc_events_compact,
        }

        try:
            l2_spec = load_prompt("L2_narrative")
            l2_prompt = build_prompt(l2_spec.body, "L2_narrative", l2_input)
            l2_output = gateway.call("L2_narrative", l2_prompt, input_data=l2_input)
        except (LLMCallError, ValueError, KeyError, OSError) as exc:
            raise DailyStrategyError(f"L2 narrative failed: {exc}") from exc

        if isinstance(l2_output, dict):
            markdown = str(l2_output.get("markdown") or "").strip()
        else:
            markdown = str(l2_output).strip()
        if not markdown:
            raise DailyStrategyError("budget L2 returned empty markdown")

        return DailyGenerationResult(
            markdown=markdown,
            misc_event_ids=tuple(misc_ids),
            clusters=tuple(cluster_records),
            merge_candidates=tuple(merge_candidates),
        )
