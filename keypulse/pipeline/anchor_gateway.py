from __future__ import annotations

import json
from datetime import date
from typing import Any

from keypulse.pipeline.daily_strategy import build_prompt
from keypulse.pipeline.model import ModelGateway, validate_schema_minimal
from keypulse.pipeline.weekly_topic_anchor import WeeklyAnchor
from keypulse.prompts.loader import load_prompt


class AnchorGatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        prompt: str,
        input_data: dict[str, Any],
        raw_response: Any,
    ) -> None:
        super().__init__(message)
        self.prompt = prompt
        self.input_data = input_data
        self.raw_response = raw_response


class AnchorGateway:
    def __init__(self, model_gateway: ModelGateway) -> None:
        self._model_gateway = model_gateway

    def assign(
        self,
        *,
        date_str: str,
        today_clusters: list[dict],
        weekly_anchors: list[WeeklyAnchor],
        known_anchors: list[WeeklyAnchor] | None = None,
    ) -> dict[str, Any]:
        spec = load_prompt("L0_anchor")
        known_anchor_list = self._select_known_anchors(
            known_anchors if known_anchors is not None else weekly_anchors
        )
        input_data = {
            "date": str(date_str),
            "today_clusters": [
                {
                    "cluster_id": str(cluster.get("cluster_id") or "").strip(),
                    "display_name": str(cluster.get("display_name") or "").strip(),
                    "narrative_one_line": str(cluster.get("narrative_one_line") or "").strip(),
                    "event_count": int(cluster.get("event_count") or 0),
                }
                for cluster in today_clusters
                if str(cluster.get("cluster_id") or "").strip()
            ],
            "weekly_anchors": [self._compact_anchor_for_prompt(anchor) for anchor in weekly_anchors],
            "known_anchors": [self._compact_anchor_for_prompt(anchor) for anchor in known_anchor_list],
        }
        prompt = build_prompt(spec.body, "L0_anchor", input_data)

        try:
            validate_schema_minimal(spec.input_schema, input_data, "$")
        except ValueError as exc:
            raise AnchorGatewayError(
                f"L0_anchor input schema validation failed: {exc}",
                prompt=prompt,
                input_data=input_data,
                raw_response=None,
            ) from exc

        raw_response: Any = None
        try:
            raw_response = self._model_gateway.call("L0_anchor", prompt, input_data=input_data)
            response_obj = self._normalize_response(raw_response)
            validate_schema_minimal(spec.output_schema, response_obj, "$")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AnchorGatewayError(
                f"L0_anchor output validation failed: {exc}",
                prompt=prompt,
                input_data=input_data,
                raw_response=raw_response,
            ) from exc

        assignments_raw = response_obj.get("assignments") or {}
        new_anchors_raw = response_obj.get("new_anchors") or []
        assignments = {str(key): str(value) for key, value in assignments_raw.items()}
        new_anchors = [dict(item) for item in new_anchors_raw if isinstance(item, dict)]
        return {"assignments": assignments, "new_anchors": new_anchors}

    @staticmethod
    def _compact_anchor_for_prompt(
        anchor: WeeklyAnchor,
        *,
        max_progress: int = 1,
        narrative_chars: int = 100,
    ) -> dict[str, Any]:
        """Dump anchor for L0_anchor prompt with daily_progress truncated.

        Why: anchors.json accumulates daily_progress entries indefinitely
        (~2KB per anchor × 90 anchors = 200KB raw → ~70k tokens). The L0_anchor
        prompt itself does not read daily_progress — it only matches today's
        cluster narratives against anchor slug/display. Keeping only the most
        recent progress entry with a truncated narrative collapses the input
        from ~120k tokens to ~5k tokens. Without this, the LLM returns empty
        responses and every cluster falls back to unanchored → things=0.
        """
        payload = anchor.to_dict()
        progress = payload.get("daily_progress") or []
        if isinstance(progress, list) and progress:
            tail = progress[-max_progress:]
            compacted: list[dict[str, Any]] = []
            for item in tail:
                if not isinstance(item, dict):
                    continue
                entry: dict[str, Any] = {}
                date_val = str(item.get("date") or "").strip()
                if date_val:
                    entry["date"] = date_val
                cluster_id = str(item.get("cluster_id") or "").strip()
                if cluster_id:
                    entry["cluster_id"] = cluster_id
                narrative = str(item.get("narrative") or "").strip()
                if narrative:
                    if len(narrative) > narrative_chars:
                        narrative = narrative[:narrative_chars].rstrip() + "…"
                    entry["narrative"] = narrative
                if entry:
                    compacted.append(entry)
            payload["daily_progress"] = compacted
        else:
            payload["daily_progress"] = []
        return payload

    @staticmethod
    def _select_known_anchors(anchors: list[WeeklyAnchor], *, max_items: int = 30) -> list[WeeklyAnchor]:
        def _parse_date(value: str) -> date:
            try:
                return date.fromisoformat(str(value or "").strip())
            except ValueError:
                return date.min

        ordered = sorted(
            list(anchors),
            key=lambda anchor: (
                _parse_date(getattr(anchor, "last_active", "")),
                _parse_date(getattr(anchor, "started", "")),
                str(getattr(anchor, "slug", "")).strip(),
            ),
            reverse=True,
        )
        return ordered[: max(int(max_items), 0)]

    @staticmethod
    def _normalize_response(raw_response: Any) -> dict[str, Any]:
        if isinstance(raw_response, dict):
            return raw_response
        if isinstance(raw_response, str):
            parsed = json.loads(raw_response)
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("L0_anchor response must be an object")
