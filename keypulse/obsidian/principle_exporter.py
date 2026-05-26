from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as date_cls, timedelta
from pathlib import Path
from typing import Any

from keypulse.utils.atomic_io import atomic_write_text


_FRONTMATTER_BOUNDARY = "---"
_PRINCIPLES_DIR = "principles"
_DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")
_KIND_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class PrincipleExportResult:
    written_paths: tuple[Path, ...]
    skipped_paths: tuple[Path, ...]
    errors: tuple[str, ...]


def load_known_principles(vault_path: str | Path) -> list[dict[str, str]]:
    principles_dir = Path(vault_path).expanduser() / _PRINCIPLES_DIR
    if not principles_dir.exists():
        return []

    known: list[dict[str, str]] = []
    for path in sorted(principles_dir.glob("*.md")):
        frontmatter = _read_frontmatter(path)
        principle_id = str(frontmatter.get("principle_id") or "").strip()
        if not principle_id:
            match = _DATE_PREFIX_RE.match(path.stem)
            principle_id = match.group(2).strip() if match else ""
        if not principle_id:
            continue
        known.append(
            {
                "principle_id": principle_id,
                "distilled": str(frontmatter.get("distilled") or "").strip(),
            }
        )
    return known


def export_principles(
    *,
    date_str: str,
    candidates: list[dict[str, Any]],
    vault_path: str | Path,
) -> PrincipleExportResult:
    principles_dir = Path(vault_path).expanduser() / _PRINCIPLES_DIR
    principles_dir.mkdir(parents=True, exist_ok=True)

    # Build a dedupe index from existing principles (same vault, all dates).
    # LLM is asked to dedupe via known_principles but doesn't always honor it
    # (esp. for cross-language paraphrases). This is the defensive backstop.
    existing_keys: set[str] = set()
    for existing_path in principles_dir.glob("*.md"):
        frontmatter = _read_frontmatter(existing_path)
        distilled = str(frontmatter.get("distilled") or "").strip()
        if distilled:
            existing_keys.add(_normalize_distilled(distilled))

    written: list[Path] = []
    skipped: list[Path] = []
    errors: list[str] = []

    for raw in candidates:
        normalized = _normalize_candidate(raw)
        if normalized is None:
            continue
        dedupe_key = _normalize_distilled(normalized["distilled"])
        if dedupe_key and dedupe_key in existing_keys:
            skipped.append(principles_dir / f"{date_str}-{normalized['slug']}.md")
            continue
        target = principles_dir / f"{date_str}-{normalized['slug']}.md"
        if target.exists():
            skipped.append(target)
            continue
        try:
            atomic_write_text(target, _render_principle_note(date_str=date_str, payload=normalized), encoding="utf-8")
        except OSError as exc:
            errors.append(f"{target}: {type(exc).__name__}:{exc}")
            continue
        written.append(target)
        if dedupe_key:
            existing_keys.add(dedupe_key)

    return PrincipleExportResult(
        written_paths=tuple(written),
        skipped_paths=tuple(skipped),
        errors=tuple(errors),
    )


_NORMALIZE_STRIP_RE = re.compile(r"[\s　\W_]+", re.UNICODE)


def _normalize_distilled(text: str) -> str:
    """Lowercase + strip punctuation/whitespace for cross-language fuzzy dedupe.

    NOTE: this only catches *near-identical* paraphrases — cross-language
    semantic equivalence (e.g. "避免过度工程化" vs "Avoid over-engineering")
    requires the LLM to honor v2 prompt's hard-dedupe rule. This backstop
    only stops obvious duplicates that slip through.
    """
    return _NORMALIZE_STRIP_RE.sub("", str(text or "").lower())


def list_week_principles(
    *,
    week_str: str,
    vault_path: str | Path,
) -> list[dict[str, str]]:
    principles_dir = Path(vault_path).expanduser() / _PRINCIPLES_DIR
    if not principles_dir.exists():
        return []

    week_dates = _week_dates(week_str)
    rows: list[dict[str, str]] = []
    seen_slugs: set[str] = set()

    for day in week_dates:
        for path in sorted(principles_dir.glob(f"{day}-*.md")):
            match = _DATE_PREFIX_RE.match(path.stem)
            if not match:
                continue
            date_text = match.group(1).strip()
            slug = match.group(2).strip()
            if not slug or slug in seen_slugs:
                continue
            frontmatter = _read_frontmatter(path)
            principle_id = str(frontmatter.get("principle_id") or slug).strip() or slug
            distilled = str(frontmatter.get("distilled") or "").strip()
            if not distilled:
                distilled = _infer_distilled_from_body(path.read_text(encoding="utf-8"))
            rows.append(
                {
                    "date": date_text,
                    "slug": slug,
                    "principle_id": principle_id,
                    "distilled": distilled,
                }
            )
            seen_slugs.add(slug)

    rows.sort(key=lambda item: (item.get("date", ""), item.get("slug", "")))
    return rows


def _normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
    slug = str(candidate.get("slug") or "").strip()
    kind = str(candidate.get("kind") or "principle").strip() or "principle"
    distilled = str(candidate.get("distilled") or "").strip()
    quote = str(candidate.get("quote") or "").strip()
    if not slug or not distilled:
        return None

    confidence_raw = candidate.get("confidence")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "slug": slug,
        "kind": kind,
        "distilled": distilled,
        "quote": quote,
        "confidence": confidence,
    }


def _render_principle_note(*, date_str: str, payload: dict[str, Any]) -> str:
    kind = str(payload.get("kind") or "principle")
    kind_tag = _kind_tag(kind)
    lines = [
        _FRONTMATTER_BOUNDARY,
        "type: principle",
        f"principle_id: {_yaml_scalar(str(payload['slug']))}",
        f"date: {_yaml_scalar(date_str)}",
        f"kind: {_yaml_scalar(kind)}",
        f"distilled: {_yaml_scalar(str(payload['distilled']))}",
        f"confidence: {float(payload.get('confidence') or 0.0):.3f}",
        f"quote: {_yaml_scalar(str(payload.get('quote') or ''))}",
        "tags:",
        f"  - {_yaml_scalar('principle')}",
        f"  - {_yaml_scalar(f'principle/{kind_tag}')}",
        _FRONTMATTER_BOUNDARY,
        "",
        f"# {payload['distilled']}",
        "",
        "## Distilled",
        str(payload["distilled"]),
        "",
        "## Source Quote",
        f"> {str(payload.get('quote') or '').strip()}" if str(payload.get("quote") or "").strip() else "> (empty)",
        "",
        "## Meta",
        f"- kind: {kind}",
        f"- confidence: {float(payload.get('confidence') or 0.0):.3f}",
        "",
    ]
    return "\n".join(lines)


def _yaml_scalar(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _kind_tag(kind: str) -> str:
    normalized = _KIND_TOKEN_RE.sub("-", str(kind).strip().lower()).strip("-")
    return normalized or "other"


def _read_frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_BOUNDARY:
        return {}

    payload: dict[str, Any] = {}
    for idx in range(1, len(lines)):
        line = lines[idx]
        if line.strip() == _FRONTMATTER_BOUNDARY:
            break
        stripped = line.strip()
        if not stripped or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        parsed = _parse_scalar(value.strip())
        payload[key.strip()] = parsed
    return payload


def _parse_scalar(value: str) -> Any:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    return raw


def _infer_distilled_from_body(markdown: str) -> str:
    lines = [line.strip() for line in str(markdown or "").splitlines() if line.strip()]
    for line in lines:
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _week_dates(week_str: str) -> list[str]:
    if "-W" not in week_str:
        return []
    year_text, week_text = week_str.split("-W", 1)
    try:
        start = date_cls.fromisocalendar(int(year_text), int(week_text), 1)
    except ValueError:
        return []
    return [(start + timedelta(days=offset)).isoformat() for offset in range(7)]
