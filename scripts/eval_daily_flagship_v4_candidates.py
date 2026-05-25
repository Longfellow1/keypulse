#!/usr/bin/env python3
"""Evaluate daily_flagship v4 candidate prompts on a real day (2026-05-06).

Outputs are written to:
  docs/daily-prompt-candidates-v4/A.md
  docs/daily-prompt-candidates-v4/B.md
  docs/daily-prompt-candidates-v4/C.md
  docs/daily-prompt-candidates-v4/meta.json
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from keypulse.config import Config
from keypulse.pipeline.daily_orchestrator import (
    _cap_flagship_events_for_prompt,
    _cluster_component_payloads,
    _extract_event_payload,
    _load_rows_for_date,
)
from keypulse.pipeline.daily_strategy import (
    _flagship_cluster_payload,
    _prepare_flagship_entity_payload,
    build_prompt,
    to_compact_event,
)
from keypulse.pipeline.entity_extractor_llm import EntityExtractorLLM
from keypulse.pipeline.model import load_model_gateway
from keypulse.prompts.loader import load_prompt
from keypulse.store.db import init_db

TARGET_DATE = "2026-05-06"
OUTPUT_DIR = Path("docs/daily-prompt-candidates-v4")
COST_PATH = Path.home() / ".keypulse" / "cost.jsonl"
CANDIDATES: tuple[tuple[str, str], ...] = (
    ("A", "daily_flagship_v4_a"),
    ("B", "daily_flagship_v4_b"),
    ("C", "daily_flagship_v4_c"),
    ("B2", "daily_flagship_v4_b2"),
    ("B3", "daily_flagship_v4_b3"),
    ("B4", "daily_flagship_v4_b4"),
    ("B5", "daily_flagship_v4_b5"),
)


def _read_cost_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _extract_cost_row(before_len: int, capability: str) -> dict[str, Any]:
    rows = _read_cost_rows(COST_PATH)
    new_rows = rows[before_len:] if len(rows) >= before_len else rows

    for row in reversed(new_rows):
        if str(row.get("capability") or "") == capability:
            return row
    for row in reversed(rows):
        if str(row.get("capability") or "") == capability:
            return row
    return {}


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _build_payload(
    *,
    date: str,
    events: list[dict[str, Any]],
    entity_output: Mapping[str, Any] | None,
    cluster_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    entity_payload = _prepare_flagship_entity_payload(entity_output)
    payload: dict[str, Any] = {
        "date": date,
        "events": [to_compact_event(event) for event in events],
        "entities": list(entity_payload.get("entities") or []),
        "event_entity_map": list(entity_payload.get("event_entity_map") or []),
        "yesterday_anchor": "",
        "recent_topic_history": [],
        "clusters": list(cluster_payloads),
    }
    return payload


def _run_candidate(
    *,
    label: str,
    capability: str,
    payload: dict[str, Any],
    gateway: Any,
) -> dict[str, Any]:
    spec = load_prompt(capability)
    prompt = build_prompt(spec.body, capability, payload)

    before_rows = _read_cost_rows(COST_PATH)
    started = time.perf_counter()
    raw_response = gateway.call(capability, prompt, input_data=payload)
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    if isinstance(raw_response, Mapping):
        markdown = str(raw_response.get("markdown") or "")
        raw_json_output: dict[str, Any] = dict(raw_response)
    else:
        markdown = str(raw_response or "")
        raw_json_output = {"markdown": markdown}

    (OUTPUT_DIR / f"{label}.md").write_text(markdown, encoding="utf-8")

    cost_row = _extract_cost_row(len(before_rows), capability)
    in_tokens = _to_int(cost_row.get("in_tokens"))
    out_tokens = _to_int(cost_row.get("out_tokens"))

    return {
        "label": label,
        "capability": capability,
        "elapsed_ms": elapsed_ms,
        "markdown_chars": len(markdown),
        "output_path": str((OUTPUT_DIR / f"{label}.md").as_posix()),
        "usage": {
            "in_tokens": in_tokens,
            "out_tokens": out_tokens,
            "total_tokens": in_tokens + out_tokens,
            "cache_hit": bool(cost_row.get("cache_hit", False)),
            "model": str(cost_row.get("model") or ""),
            "tier": str(cost_row.get("tier") or ""),
            "prompt_version": str(cost_row.get("prompt_version") or ""),
            "ts": str(cost_row.get("ts") or ""),
        },
        "raw_json_output": raw_json_output,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cfg = Config.load()
    init_db(cfg.db_path_expanded)

    rows = _load_rows_for_date(TARGET_DATE)
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            events.append(_extract_event_payload(row))
        except ValueError:
            continue

    capped_events, was_capped = _cap_flagship_events_for_prompt(events)

    gateway = load_model_gateway(cfg)

    extractor = EntityExtractorLLM(gateway)
    entity_result = extractor.extract_entities(date=TARGET_DATE, events=capped_events)
    entity_output = entity_result.to_dict()

    components, _merge_pairs = _cluster_component_payloads(capped_events, cfg.pipeline.value_density)
    cluster_payloads = [
        cluster
        for cluster in (_flagship_cluster_payload(component) for component in components)
        if cluster
    ]

    payload = _build_payload(
        date=TARGET_DATE,
        events=capped_events,
        entity_output=entity_output,
        cluster_payloads=cluster_payloads,
    )

    only_labels = {x.strip() for x in (os.environ.get("ONLY_LABELS") or "").split(",") if x.strip()}
    candidates_to_run = [c for c in CANDIDATES if not only_labels or c[0] in only_labels]

    results: list[dict[str, Any]] = []
    for label, capability in candidates_to_run:
        result = _run_candidate(label=label, capability=capability, payload=payload, gateway=gateway)
        results.append(result)
        usage = result["usage"]
        print(
            "[eval_daily_flagship_v4_candidates] "
            f"{label} capability={capability} "
            f"elapsed_ms={result['elapsed_ms']} "
            f"tokens={usage['in_tokens']}+{usage['out_tokens']} "
            f"cache_hit={usage['cache_hit']}"
        )

    meta = {
        "date": TARGET_DATE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "raw_events": len(events),
            "capped_events": len(capped_events),
            "was_capped": bool(was_capped),
            "cluster_count": len(cluster_payloads),
            "entities_count": len(payload.get("entities") or []),
            "event_entity_map_count": len(payload.get("event_entity_map") or []),
        },
        "candidates": results,
    }
    (OUTPUT_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"[eval_daily_flagship_v4_candidates] wrote {(OUTPUT_DIR / 'meta.json').as_posix()}")


if __name__ == "__main__":
    main()
