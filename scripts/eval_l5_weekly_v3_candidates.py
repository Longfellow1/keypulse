#!/usr/bin/env python3
"""Evaluate L5 weekly v3 prompt candidates on a target ISO week.

Example:
  python3 scripts/eval_l5_weekly_v3_candidates.py --all --week 2026-W19 --clear-cache
"""

from __future__ import annotations

import argparse
import json
import shutil
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from keypulse.config import Config
from keypulse.pipeline import model as model_module
from keypulse.pipeline import weekly_orchestrator
from keypulse.prompts import loader as prompt_loader_module
from keypulse.store.db import init_db
from keypulse.store.repository import get_state

TARGET_CAPABILITY = "L5_weekly_main_narrative"
DEFAULT_WEEK = "2026-W19"
OUTPUT_DIR = Path("docs/weekly-prompt-candidates-v3")
CANDIDATE_PATHS: tuple[Path, ...] = (
    Path("keypulse/prompts/candidates/L5_weekly_v3_a.md"),
    Path("keypulse/prompts/candidates/L5_weekly_v3_b.md"),
    Path("keypulse/prompts/candidates/L5_weekly_v3_c.md"),
)
ANTIPATTERN_PROBES: tuple[str, ...] = (
    "## TL;DR",
    "→ 这意味着",
    "整理成下周可复现的执行入口",
    "本周没有可确认的关键决策",
    "本周没有可确认的可见产出",
    "暂无可确认的",
    "有连续记录",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def _short_candidate_name(path: Path) -> str:
    stem = path.stem.lower()
    if stem.endswith("_a"):
        return "a"
    if stem.endswith("_b"):
        return "b"
    if stem.endswith("_c"):
        return "c"
    return stem


def _build_week_patterns(week: str) -> set[str]:
    patterns = {week}
    for day in weekly_orchestrator._week_dates(week):
        patterns.add(day)
    return patterns


def _clear_l5_llm_cache(week: str) -> int:
    cache_dir = Path.home() / ".keypulse" / "cache" / "llm"
    if not cache_dir.exists():
        return 0
    patterns = _build_week_patterns(week)
    removed = 0
    for path in cache_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        capability = str(payload.get("capability") or "").strip()
        input_payload = payload.get("input")
        input_data = input_payload.get("input_data") if isinstance(input_payload, dict) else None
        if not capability and isinstance(input_payload, dict):
            capability = str(input_payload.get("capability") or "").strip()
        if capability != TARGET_CAPABILITY:
            continue
        serialized = json.dumps(input_data, ensure_ascii=False, sort_keys=True) if isinstance(input_data, dict) else ""
        if not any(pattern in serialized for pattern in patterns):
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _clear_weekly_wrapper_cache(week: str) -> int:
    weekly_cache_dir = Path.home() / ".keypulse" / "weekly-cache" / week
    if not weekly_cache_dir.exists():
        return 0
    removed = 0
    for child in weekly_cache_dir.glob("*"):
        try:
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _reset_weekly_runtime_state() -> None:
    weekly_orchestrator._WEEKLY_MEMORY_CACHE.clear()
    weekly_orchestrator._WEEKLY_CIRCUIT.failures = 0
    weekly_orchestrator._WEEKLY_CIRCUIT.open_until = 0.0


@contextmanager
def _override_l5_prompt(candidate_path: Path) -> Iterator[None]:
    candidate_spec = prompt_loader_module._parse_prompt_file(candidate_path)
    override_spec = replace(candidate_spec, capability=TARGET_CAPABILITY)

    original_loader_load_prompt = prompt_loader_module.load_prompt
    original_model_load_prompt = model_module.load_prompt
    original_weekly_load_prompt = weekly_orchestrator.load_prompt

    def patched_load_prompt(capability: str):
        if str(capability).strip() == TARGET_CAPABILITY:
            return override_spec
        return original_loader_load_prompt(capability)

    prompt_loader_module.load_prompt = patched_load_prompt
    model_module.load_prompt = patched_load_prompt
    weekly_orchestrator.load_prompt = patched_load_prompt
    try:
        yield
    finally:
        prompt_loader_module.load_prompt = original_loader_load_prompt
        model_module.load_prompt = original_model_load_prompt
        weekly_orchestrator.load_prompt = original_weekly_load_prompt


@contextmanager
def _capture_l5_outputs() -> Iterator[list[dict[str, Any]]]:
    captured: list[dict[str, Any]] = []
    original_call = weekly_orchestrator._call_weekly_llm

    def wrapped_call(**kwargs):
        result = original_call(**kwargs)
        capability = str(kwargs.get("capability") or "")
        if capability == TARGET_CAPABILITY:
            content = result.content if isinstance(result.content, dict) else {}
            narrative = str(content.get("narrative") or "").strip() if isinstance(content, dict) else ""
            captured.append(
                {
                    "slug": str(content.get("slug") or "").strip() if isinstance(content, dict) else "",
                    "narrative_chars": len(narrative),
                    "source": str(result.source),
                    "attempts": int(result.attempts),
                    "reason": str(result.reason or ""),
                }
            )
        return result

    weekly_orchestrator._call_weekly_llm = wrapped_call
    try:
        yield captured
    finally:
        weekly_orchestrator._call_weekly_llm = original_call


def _cost_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    in_tokens = sum(int(row.get("in_tokens") or 0) for row in rows)
    out_tokens = sum(int(row.get("out_tokens") or 0) for row in rows)
    cost_usd = round(sum(float(row.get("cost_usd") or 0.0) for row in rows), 8)
    by_source: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        by_source[source] = by_source.get(source, 0) + 1
    return {
        "rows": len(rows),
        "in_tokens": in_tokens,
        "out_tokens": out_tokens,
        "total_tokens": in_tokens + out_tokens,
        "cost_usd": cost_usd,
        "by_source": by_source,
    }


def _antipattern_hits(markdown: str) -> dict[str, int]:
    text = str(markdown or "")
    return {probe: text.count(probe) for probe in ANTIPATTERN_PROBES if text.count(probe) > 0}


def _read_weekly_outcome() -> dict[str, Any]:
    raw = get_state("weekly_last_outcome") or ""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_style(
    *,
    week: str,
    style: str,
    candidate_path: Path,
    output_markdown_path: Path,
) -> dict[str, Any]:
    cost_path = Path.home() / ".keypulse" / "cost.jsonl"
    before_cost = _read_jsonl(cost_path)

    with _override_l5_prompt(candidate_path):
        with _capture_l5_outputs() as l5_outputs:
            weekly_path = weekly_orchestrator.run_weekly(week, style=style)

    after_cost = _read_jsonl(cost_path)
    cost_delta = after_cost[len(before_cost) :] if len(after_cost) >= len(before_cost) else after_cost

    weekly_file = Path(weekly_path)
    markdown = weekly_file.read_text(encoding="utf-8") if weekly_file.exists() else ""
    output_markdown_path.write_text(markdown, encoding="utf-8")

    outcome = _read_weekly_outcome()
    breakdown = outcome.get("quality_breakdown") if isinstance(outcome.get("quality_breakdown"), dict) else {}

    return {
        "style": style,
        "weekly_path": str(weekly_file),
        "copied_to": str(output_markdown_path),
        "quality_total": int(breakdown.get("total") or 0),
        "validator_failures": int(outcome.get("validator_failures") or 0),
        "outcome": str(outcome.get("outcome") or ""),
        "cost": _cost_summary(cost_delta),
        "l5_topic_metrics": l5_outputs,
        "antipattern_hits": _antipattern_hits(markdown),
        "markdown_chars": len(markdown),
    }


def _evaluate_one(
    *,
    week: str,
    candidate_path: Path,
    run_index: int,
    clear_cache: bool,
    output_dir: Path,
) -> dict[str, Any]:
    if clear_cache:
        removed_llm = _clear_l5_llm_cache(week)
        removed_weekly = _clear_weekly_wrapper_cache(week)
    else:
        removed_llm = 0
        removed_weekly = 0

    _reset_weekly_runtime_state()
    label = _short_candidate_name(candidate_path)
    style_results: list[dict[str, Any]] = []
    for style in ("exec", "plain"):
        output_markdown_path = output_dir / f"{label}_run{run_index}_{style}.md"
        result = _run_style(
            week=week,
            style=style,
            candidate_path=candidate_path,
            output_markdown_path=output_markdown_path,
        )
        style_results.append(result)

    metadata = {
        "candidate": str(candidate_path),
        "candidate_label": label,
        "run_index": run_index,
        "week": week,
        "cache_clear": {
            "enabled": clear_cache,
            "removed_llm_files": removed_llm,
            "removed_weekly_cache_entries": removed_weekly,
        },
        "styles": style_results,
    }
    metadata_path = output_dir / f"{label}_run{run_index}.meta.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return metadata


def _resolve_candidates(args: argparse.Namespace) -> list[Path]:
    if args.all:
        return list(CANDIDATE_PATHS)
    if args.candidate:
        return [Path(args.candidate)]
    raise SystemExit("Please provide --candidate PATH or --all.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate L5 weekly v3 prompt candidates.")
    parser.add_argument("--candidate", type=str, default="", help="Path to one candidate prompt markdown file.")
    parser.add_argument("--all", action="store_true", help="Run all built-in candidates (a/b/c).")
    parser.add_argument("--week", type=str, default=DEFAULT_WEEK, help="ISO week, e.g. 2026-W19.")
    parser.add_argument("--run", type=int, default=1, help="Run index for single-candidate mode.")
    parser.add_argument("--runs", type=int, default=3, help="How many runs per candidate when using --all.")
    parser.add_argument("--clear-cache", action="store_true", help="Clear related L5 caches before each run.")
    parser.add_argument("--output-dir", type=str, default=str(OUTPUT_DIR), help="Output directory for markdown/meta artifacts.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config.load()
    init_db(cfg.db_path_expanded)

    candidates = _resolve_candidates(args)
    for candidate_path in candidates:
        if not candidate_path.exists():
            raise FileNotFoundError(f"candidate prompt not found: {candidate_path}")
        prompt_loader_module._parse_prompt_file(candidate_path)

    reports: list[dict[str, Any]] = []
    if args.all:
        run_count = max(1, int(args.runs))
        for candidate in candidates:
            for run_index in range(1, run_count + 1):
                report = _evaluate_one(
                    week=args.week,
                    candidate_path=candidate,
                    run_index=run_index,
                    clear_cache=args.clear_cache,
                    output_dir=output_dir,
                )
                reports.append(report)
                print(
                    f"[eval_l5_weekly_v3_candidates] candidate={report['candidate_label']} run={run_index} "
                    f"exec_score={report['styles'][0]['quality_total']} plain_score={report['styles'][1]['quality_total']}"
                )
    else:
        report = _evaluate_one(
            week=args.week,
            candidate_path=candidates[0],
            run_index=max(1, int(args.run)),
            clear_cache=args.clear_cache,
            output_dir=output_dir,
        )
        reports.append(report)
        print(
            f"[eval_l5_weekly_v3_candidates] candidate={report['candidate_label']} run={report['run_index']} "
            f"exec_score={report['styles'][0]['quality_total']} plain_score={report['styles'][1]['quality_total']}"
        )

    summary_path = output_dir / "meta.json"
    summary = {
        "week": args.week,
        "generated_reports": len(reports),
        "reports": reports,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"[eval_l5_weekly_v3_candidates] wrote {summary_path.as_posix()}")


if __name__ == "__main__":
    main()
