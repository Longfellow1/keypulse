from __future__ import annotations

import json
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
    ) -> dict[str, Any]:
        spec = load_prompt("L0_anchor")
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
            "weekly_anchors": [anchor.to_dict() for anchor in weekly_anchors],
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
    def _normalize_response(raw_response: Any) -> dict[str, Any]:
        if isinstance(raw_response, dict):
            return raw_response
        if isinstance(raw_response, str):
            parsed = json.loads(raw_response)
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("L0_anchor response must be an object")
