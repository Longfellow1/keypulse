from __future__ import annotations

import json
import re
from typing import Any

from keypulse.pipeline.daily_strategy import build_prompt
from keypulse.pipeline.model import ModelGateway, validate_schema_minimal
from keypulse.prompts.loader import load_prompt


_SLUG_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class PrincipleDistillationError(RuntimeError):
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


class PrincipleDistillationGateway:
    def __init__(self, model_gateway: ModelGateway) -> None:
        self._model_gateway = model_gateway

    def distill(
        self,
        *,
        date_str: str,
        keyboard_chunks: list[dict[str, Any]],
        known_principles: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        spec = load_prompt("L7_principle_distillation")
        input_data = {
            "date": str(date_str).strip(),
            "keyboard_chunks": [self._normalize_chunk(chunk) for chunk in keyboard_chunks if isinstance(chunk, dict)],
            "known_principles": [
                {
                    "principle_id": str(item.get("principle_id") or "").strip(),
                    "distilled": str(item.get("distilled") or "").strip(),
                }
                for item in known_principles
                if isinstance(item, dict) and str(item.get("principle_id") or "").strip()
            ],
        }
        prompt = build_prompt(spec.body, "L7_principle_distillation", input_data)

        try:
            validate_schema_minimal(spec.input_schema, input_data, "$")
        except ValueError as exc:
            raise PrincipleDistillationError(
                f"L7_principle_distillation input schema validation failed: {exc}",
                prompt=prompt,
                input_data=input_data,
                raw_response=None,
            ) from exc

        raw_response: Any = None
        try:
            raw_response = self._model_gateway.call(
                "L7_principle_distillation",
                prompt,
                input_data=input_data,
            )
            response_obj = self._normalize_response(raw_response)
            validate_schema_minimal(spec.output_schema, response_obj, "$")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise PrincipleDistillationError(
                f"L7_principle_distillation output validation failed: {exc}",
                prompt=prompt,
                input_data=input_data,
                raw_response=raw_response,
            ) from exc

        candidates = response_obj.get("candidates") if isinstance(response_obj, dict) else []
        if not isinstance(candidates, list):
            return []
        return [self._normalize_candidate(item) for item in candidates if isinstance(item, dict)]

    @staticmethod
    def _normalize_response(raw_response: Any) -> dict[str, Any]:
        if isinstance(raw_response, dict):
            return raw_response
        if isinstance(raw_response, str):
            parsed = json.loads(raw_response)
            if isinstance(parsed, dict):
                return parsed
        raise ValueError("L7_principle_distillation response must be an object")

    @staticmethod
    def _normalize_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(chunk.get("id") or "").strip(),
            "ts_start": str(chunk.get("ts_start") or "").strip(),
            "app_name": str(chunk.get("app_name") or "").strip(),
            "window_title": str(chunk.get("window_title") or "").strip(),
            "content": str(chunk.get("content") or chunk.get("content_text") or "").strip(),
        }

    @staticmethod
    def _normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
        distilled = str(candidate.get("distilled") or "").strip()
        quote = str(candidate.get("quote") or "").strip()
        raw_slug = str(candidate.get("slug") or "").strip().lower()
        slug = raw_slug if _SLUG_RE.fullmatch(raw_slug) else _fallback_slug(distilled, quote)
        kind = str(candidate.get("kind") or "principle").strip() or "principle"
        confidence = float(candidate.get("confidence") or 0.0)
        return {
            "slug": slug,
            "kind": kind,
            "distilled": distilled,
            "quote": quote,
            "confidence": max(0.0, min(1.0, confidence)),
        }


def _fallback_slug(distilled: str, quote: str) -> str:
    base = " ".join([distilled, quote]).lower()
    tokens = _SLUG_TOKEN_RE.findall(base)
    if not tokens:
        return "principle-note"
    slug = "-".join(tokens[:8]).strip("-")[:80].strip("-")
    if not slug:
        return "principle-note"
    if not slug[0].isalpha():
        slug = f"principle-{slug}"[:80].strip("-")
    return slug or "principle-note"
