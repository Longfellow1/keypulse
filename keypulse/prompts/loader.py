from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PromptCapabilityNotFoundError(LookupError):
    """Raised when no prompt file matches the requested capability."""


class PromptFormatError(ValueError):
    """Raised when a prompt file has invalid frontmatter or schema references."""


@dataclass(frozen=True)
class PromptSpec:
    capability: str
    version: str
    model_tier: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    max_tokens: int
    temperature: float
    body: str


_PROMPTS_DIR = Path(__file__).resolve().parent
_ALIAS_MAP = {
    "l1": "L1_cluster_review",
    "l2": "L2_narrative",
    "l3": "L3_topic_naming",
    "l4": "L4_weekly_reconcile",
    "l5": "L5_weekly_main_narrative",
    "l6": "L6_explorer",
}
_REQUIRED_FIELDS = {
    "capability",
    "version",
    "model_tier",
    "input_schema",
    "output_schema",
    "max_tokens",
    "temperature",
}


def _normalize_capability(capability: str) -> str:
    raw = capability.strip()
    if not raw:
        return raw
    return _ALIAS_MAP.get(raw.lower(), raw)


def _parse_frontmatter(raw_text: str, source: Path) -> tuple[dict[str, str], str]:
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PromptFormatError(f"missing frontmatter start in {source}")

    end_index: int | None = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_index = idx
            break
    if end_index is None:
        raise PromptFormatError(f"missing frontmatter end in {source}")

    meta: dict[str, str] = {}
    for line_no, line in enumerate(lines[1:end_index], start=2):
        stripped = line.strip()
        if not stripped:
            continue
        if ":" not in stripped:
            raise PromptFormatError(f"invalid frontmatter line {line_no} in {source}: {line}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise PromptFormatError(f"invalid frontmatter key/value line {line_no} in {source}: {line}")
        meta[key] = value

    missing = sorted(_REQUIRED_FIELDS - set(meta.keys()))
    if missing:
        raise PromptFormatError(f"missing frontmatter fields in {source}: {', '.join(missing)}")

    body = "\n".join(lines[end_index + 1 :]).strip()
    return meta, body


def _load_schema(schema_rel_path: str, source: Path) -> dict[str, Any]:
    schema_path = (_PROMPTS_DIR / schema_rel_path).resolve()
    try:
        schema_path.relative_to(_PROMPTS_DIR)
    except ValueError as exc:
        raise PromptFormatError(f"schema path escapes prompts dir: {schema_rel_path}") from exc

    if not schema_path.exists():
        raise PromptFormatError(f"schema file not found for {source}: {schema_rel_path}")

    try:
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromptFormatError(f"invalid schema JSON {schema_rel_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise PromptFormatError(f"schema must be JSON object: {schema_rel_path}")
    return payload


def _parse_prompt_file(path: Path) -> PromptSpec:
    raw_text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw_text, path)
    capability = str(meta["capability"]).strip()
    version = str(meta["version"]).strip()
    model_tier = str(meta["model_tier"]).strip()

    try:
        max_tokens = int(str(meta["max_tokens"]).strip())
    except ValueError as exc:
        raise PromptFormatError(f"max_tokens must be int in {path}") from exc

    try:
        temperature = float(str(meta["temperature"]).strip())
    except ValueError as exc:
        raise PromptFormatError(f"temperature must be float in {path}") from exc

    return PromptSpec(
        capability=capability,
        version=version,
        model_tier=model_tier,
        input_schema=_load_schema(str(meta["input_schema"]).strip(), path),
        output_schema=_load_schema(str(meta["output_schema"]).strip(), path),
        max_tokens=max_tokens,
        temperature=temperature,
        body=body,
    )


def load_prompt(capability: str) -> PromptSpec:
    resolved_capability = _normalize_capability(capability)
    candidates: list[tuple[Path, PromptSpec]] = []
    for path in sorted(_PROMPTS_DIR.glob("*.md")):
        spec = _parse_prompt_file(path)
        if spec.capability == resolved_capability:
            candidates.append((path, spec))
    if not candidates:
        raise PromptCapabilityNotFoundError(f"prompt capability not found: {capability}")
    # Return the latest version (last in sorted order)
    return candidates[-1][1]
