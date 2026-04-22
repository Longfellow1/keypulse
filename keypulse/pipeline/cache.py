from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(item.get("title") or ""),
        "body": str(item.get("body") or ""),
        "score": round(float(item.get("score") or 0.0), 4),
        "topic": str(item.get("topic") or ""),
        "source": str(item.get("source") or ""),
    }


def candidate_cache_key(items: list[dict[str, Any]]) -> str:
    canonical = sorted(
        (_canonical_item(item) for item in items),
        key=lambda item: (item["title"], item["body"], item["score"], item["topic"], item["source"]),
    )
    payload = json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
