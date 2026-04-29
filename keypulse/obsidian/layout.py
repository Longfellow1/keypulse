from __future__ import annotations

import re
import json
from datetime import datetime
from typing import Any


_slug_re = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def slugify(value: str, fallback: str = "item", max_length: int | None = None) -> str:
    cleaned = value.strip().lower()
    cleaned = cleaned.replace("/", "-")
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    cleaned = _slug_re.sub("-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    result = cleaned or fallback
    if max_length is not None and len(result) > max_length:
        result = result[:max_length].rstrip("-") or fallback
    return result


def iso_date(value: str | None) -> str:
    if not value:
        return datetime.now().date().isoformat()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return value[:10]


def time_token(value: str | None) -> str:
    if not value:
        return "0000"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%H%M")
    except Exception:
        return "0000"


def render_frontmatter(properties: dict[str, Any]) -> str:
    def format_scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            if re.fullmatch(r"[A-Za-z0-9._-]+", value):
                return value
            return json.dumps(value, ensure_ascii=False)
        return json.dumps(value, ensure_ascii=False)

    lines = ["---"]
    for key, value in properties.items():
        if value is None:
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                lines.append(f"  - {format_scalar(item)}")
        else:
            lines.append(f"{key}: {format_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def render_note(properties: dict[str, Any], body: str) -> str:
    return f"{render_frontmatter(properties)}\n\n{body.strip()}\n"
