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
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from keypulse.pipeline.model import LLMCallError, ModelGateway


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


@dataclass(frozen=True)
class DailyGenerationResult:
    """Strategy output. Orchestrator handles all writes; strategy only computes."""
    markdown: str
    misc_event_ids: tuple[str, ...] = ()
    clusters: tuple[ClusterRecord, ...] = ()
    merge_candidates: tuple[tuple[str, str], ...] = ()


def build_prompt(spec_body: str, capability: str, payload: Mapping[str, Any]) -> str:
    """Build the same `CAPABILITY: ... <<INPUT_JSON>> ...` envelope the orchestrator uses."""
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


def to_compact_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Orchestrator payload → flagship/budget L2 compact form `{t, s, a, c, sp}`.

    Truncates content to 240 chars (matches /tmp eval extractor) — keeps the
    full-day prompt within tokens budget while preserving signal density.
    """
    ts = str(event.get("ts_start") or "")
    hhmm = ts[11:16] if len(ts) >= 16 and ts[10] == "T" else ts[:5]
    out: dict[str, Any] = {
        "t": hhmm,
        "s": str(event.get("source") or "ax_text"),
        "c": (str(event.get("content_text") or "").strip())[:240],
    }
    app = str(event.get("app_name") or "").strip()
    if app:
        out["a"] = app
    speaker = str(event.get("speaker") or "").strip()
    if speaker:
        out["sp"] = speaker
    return out


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
    ) -> DailyGenerationResult:
        """Turn full-day events into a daily.md markdown + structural metadata.

        Implementations may raise DailyStrategyError on unrecoverable failure.
        """
        ...


class FlagshipSingleStepStrategy(DailyStrategy):
    """One large-model call → finished daily.md. No clustering / no topics."""

    name = "flagship"
    capability = "daily_flagship"

    def generate(
        self,
        *,
        date_str: str,
        events: list[dict[str, Any]],
        gateway: ModelGateway,
    ) -> DailyGenerationResult:
        from keypulse.prompts.loader import load_prompt

        compact = [to_compact_event(event) for event in events]
        payload: dict[str, Any] = {"date": date_str, "events": compact}

        try:
            spec = load_prompt(self.capability)
            prompt = build_prompt(spec.body, self.capability, payload)
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
    ) -> DailyGenerationResult:
        from keypulse.prompts.loader import load_prompt

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
                )
            )
            l2_clusters.append(
                {
                    "component_id": component_id,
                    "display_name": display_name,
                    "topic_action": action,
                    "peak_event_density": peak_event_density,
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
