"""主线锚定层 — daily 与 weekly 之间的运行时状态。

设计文档: docs/golden-daily/SCHEMA.md
落盘: ~/.keypulse/anchors.json

核心契约：
- daily 跑前 load_weekly_anchors，跑完 save_weekly_anchors
- anchor_today_clusters 委托 gateway（LLM/mock）一次性归并今日 events → assignments
- update_anchors_with_assignments 维护主线表（candidate→active→stale）
- 单 event 在没显式归到现有主线时一律 unanchored，避免噪音升格
"""

from __future__ import annotations

import json
import inspect
import re
from dataclasses import asdict, dataclass, field
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

AnchorState = Literal["active", "candidate", "completed", "stale", "dormant"]

_DEFAULT_PATH = Path.home() / ".keypulse" / "anchors.json"
_LEGACY_PATH = Path.home() / ".keypulse" / "weekly-anchor.json"
_SCHEMA_VERSION = 2
_CANDIDATE_PROMOTE_DAYS = 2
_STALE_DAYS = 5
_PROJECT_ALIAS: dict[str, str] = {
    # "hud": "keypulse",
}
_INVALID_FILENAME_CHARS_RE = re.compile(r'[\/\\:*?"<>|]')
_MAX_FILENAME_LENGTH = 100


def _runtime_default_path() -> Path:
    if _DEFAULT_PATH.name == "anchors.json" and _DEFAULT_PATH.parent.name == ".keypulse":
        return Path.home() / ".keypulse" / "anchors.json"
    return _DEFAULT_PATH


def _runtime_legacy_path() -> Path:
    if _LEGACY_PATH.name == "weekly-anchor.json" and _LEGACY_PATH.parent.name == ".keypulse":
        return Path.home() / ".keypulse" / "weekly-anchor.json"
    return _LEGACY_PATH


@dataclass
class WeeklyAnchor:
    slug: str
    display: str
    started: str
    last_active: str
    state: AnchorState
    daily_progress: list[dict] = field(default_factory=list)
    candidate_for_days: int = 0
    timeline_entries: list[dict[str, str]] = field(default_factory=list)
    derived_from: str | None = None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["timeline_entries"] = _dedupe_timeline_entries(self.timeline_entries)
        payload["derived_from"] = str(self.derived_from or "").strip() or None
        return payload

    @classmethod
    def from_dict(cls, d: dict) -> "WeeklyAnchor":
        daily_progress = [dict(item) for item in d.get("daily_progress", []) if isinstance(item, dict)]
        timeline_entries_raw = d.get("timeline_entries")
        if isinstance(timeline_entries_raw, list):
            timeline_entries = _dedupe_timeline_entries(timeline_entries_raw)
        else:
            timeline_entries = _timeline_entries_from_daily_progress(daily_progress)
        return cls(
            slug=d["slug"],
            display=d.get("display", ""),
            started=d.get("started", ""),
            last_active=d.get("last_active", d.get("started", "")),
            state=d.get("state", "active"),
            daily_progress=daily_progress,
            candidate_for_days=int(d.get("candidate_for_days", 0)),
            timeline_entries=timeline_entries,
            derived_from=str(d.get("derived_from") or "").strip() or None,
        )


class AnchorGateway(Protocol):
    def assign(
        self,
        *,
        date_str: str,
        today_clusters: list[dict],
        weekly_anchors: list[WeeklyAnchor],
        known_anchors: list[WeeklyAnchor] | None = None,
    ) -> dict[str, Any]:
        ...


def _timeline_entry(date_text: str, summary: str, daily_ref: str | None = None) -> dict[str, str]:
    date_norm = str(date_text or "").strip()
    summary_norm = str(summary or "").strip()
    if not date_norm:
        return {}
    if not summary_norm:
        return {}
    return {
        "date": date_norm,
        "summary": summary_norm,
        "daily_ref": str(daily_ref or f"[[{date_norm}]]").strip() or f"[[{date_norm}]]",
    }


def _dedupe_timeline_entries(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    by_date: dict[str, dict[str, str]] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        normalized = _timeline_entry(
            str(raw.get("date") or "").strip(),
            str(raw.get("summary") or "").strip(),
            str(raw.get("daily_ref") or "").strip() or None,
        )
        if not normalized:
            continue
        by_date[normalized["date"]] = normalized
    return [by_date[key] for key in sorted(by_date.keys())]


def _timeline_entries_from_daily_progress(daily_progress: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in daily_progress:
        if not isinstance(item, dict):
            continue
        row = _timeline_entry(
            str(item.get("date") or "").strip(),
            str(item.get("narrative") or "").strip(),
        )
        if row:
            rows.append(row)
    return _dedupe_timeline_entries(rows)


def upsert_anchor_timeline_entry(
    anchor: WeeklyAnchor,
    *,
    date_str: str,
    summary: str,
    daily_ref: str | None = None,
) -> None:
    row = _timeline_entry(date_str, summary, daily_ref)
    if not row:
        return
    existing = [item for item in anchor.timeline_entries if isinstance(item, dict)]
    existing.append(row)
    anchor.timeline_entries = _dedupe_timeline_entries(existing)


def _yaml_scalar(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _project_slug_tag(anchor_slug: str) -> str:
    token = str(anchor_slug or "").split("-", 1)[0].strip().lower()
    aliased = str(_PROJECT_ALIAS.get(token, token) or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9]", "", aliased)
    if not normalized or normalized.isdigit():
        return ""
    return f"project/{normalized}"


def _anchor_tags(anchor: WeeklyAnchor) -> list[str]:
    tags = ["anchor"]
    state = str(anchor.state or "").strip().lower()
    if state:
        tags.append(f"anchor/{state}")
    project_tag = _project_slug_tag(anchor.slug)
    if project_tag:
        tags.append(project_tag)
    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        key = str(tag).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _sanitize_filename(display: str, fallback: str) -> str:
    fallback_value = str(fallback or "").strip()
    candidate = str(display or "").strip()
    if not candidate:
        candidate = fallback_value

    sanitized = _INVALID_FILENAME_CHARS_RE.sub("-", candidate)
    sanitized = sanitized.strip(" .")
    if len(sanitized) > _MAX_FILENAME_LENGTH:
        sanitized = sanitized[:_MAX_FILENAME_LENGTH].rstrip(" .")
    if not sanitized:
        return fallback_value
    return sanitized


def anchor_note_filename(display: str, fallback_slug: str) -> str:
    base = _sanitize_filename(display, fallback_slug)
    return f"{base}.md"


def render_anchor_note(
    anchor: WeeklyAnchor,
    *,
    anchor_filename_by_slug: dict[str, str] | None = None,
) -> str:
    derived_raw = str(anchor.derived_from or "").strip()
    derived_target = (
        str((anchor_filename_by_slug or {}).get(derived_raw) or derived_raw).strip()
        if derived_raw
        else ""
    )
    derived_value = f"[[{derived_target}]]" if derived_target else ""
    display_value = str(anchor.display or anchor.slug)
    alias_value = str(anchor.display or "").strip()
    tags = _anchor_tags(anchor)
    lines = [
        "---",
        f"anchor_id: {_yaml_scalar(anchor.slug)}",
        f"display: {_yaml_scalar(display_value)}",
    ]
    if alias_value:
        lines.extend(
            [
                "aliases:",
                f"  - {_yaml_scalar(alias_value)}",
            ]
        )
    lines.extend(
        [
        f"started: {_yaml_scalar(anchor.started)}",
        f"last_active: {_yaml_scalar(anchor.last_active)}",
        f"state: {_yaml_scalar(anchor.state)}",
        "tags:",
        *[f"  - {_yaml_scalar(tag)}" for tag in tags],
        f"derived_from: {_yaml_scalar(derived_value)}",
        "---",
        "",
        "## Timeline",
        ]
    )
    for row in _dedupe_timeline_entries(anchor.timeline_entries):
        date_text = str(row.get("date") or "").strip()
        summary = str(row.get("summary") or "").strip()
        daily_ref = str(row.get("daily_ref") or f"[[{date_text}]]").strip() or f"[[{date_text}]]"
        if not date_text or not summary:
            continue
        lines.append(f"- {date_text} {summary} → {daily_ref}")
    lines.append("")
    return "\n".join(lines)


def write_anchor_note(
    anchor: WeeklyAnchor,
    *,
    vault_path: Path,
    anchor_filename_by_slug: dict[str, str] | None = None,
) -> Path:
    anchors_dir = Path(vault_path).expanduser() / "anchors"
    anchors_dir.mkdir(parents=True, exist_ok=True)
    target = anchors_dir / anchor_note_filename(anchor.display, anchor.slug)
    target.write_text(
        render_anchor_note(anchor, anchor_filename_by_slug=anchor_filename_by_slug),
        encoding="utf-8",
    )
    return target


def _state_payload(week_str: str, anchors: list[WeeklyAnchor]) -> dict:
    return {
        "schema_version": _SCHEMA_VERSION,
        "week": week_str,
        "anchors": [a.to_dict() for a in anchors],
        "saved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def migrate_legacy_weekly_anchor_file(
    *,
    legacy_path: Path | None = None,
    target_path: Path | None = None,
    week_str: str = "",
) -> bool:
    legacy = legacy_path or _runtime_legacy_path()
    target = target_path or _runtime_default_path()
    if not legacy.exists():
        return False
    if target.exists():
        return False

    try:
        payload = json.loads(legacy.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(payload, dict):
        return False

    anchors_raw = payload.get("anchors")
    if not isinstance(anchors_raw, list):
        anchors_raw = []

    anchors: list[WeeklyAnchor] = []
    for item in anchors_raw:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        anchors.append(WeeklyAnchor.from_dict(item))

    resolved_week = str(payload.get("week") or week_str or "").strip()
    save_weekly_anchors(resolved_week, anchors, path=target)
    return True


def load_weekly_anchors(week_str: str, *, path: Path | None = None) -> list[WeeklyAnchor]:
    """读 ~/.keypulse/anchors.json。兼容旧 weekly-anchor.json 一次性平迁。"""
    p = path or _runtime_default_path()
    if path is None and not p.exists():
        migrate_legacy_weekly_anchor_file(week_str=week_str)
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    anchors_raw = payload.get("anchors")
    if not isinstance(anchors_raw, list):
        return []
    anchors: list[WeeklyAnchor] = []
    for item in anchors_raw:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug:
            continue
        anchors.append(WeeklyAnchor.from_dict(item))
    return anchors


def save_weekly_anchors(
    week_str: str,
    anchors: list[WeeklyAnchor],
    *,
    path: Path | None = None,
) -> None:
    p = path or _runtime_default_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_state_payload(week_str, anchors), ensure_ascii=False, indent=2), encoding="utf-8")


def anchor_today_clusters(
    *,
    date_str: str,
    today_clusters: list[dict],
    weekly_anchors: list[WeeklyAnchor],
    known_anchors: list[WeeklyAnchor] | None = None,
    gateway: AnchorGateway | Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """委托 gateway 做归并，返回 {assignments, new_anchors}。

    gateway 可以是含 .assign 方法的对象，或 callable。
    """
    known = known_anchors if known_anchors is not None else weekly_anchors
    if hasattr(gateway, "assign"):
        assigner = gateway.assign
        accepts_known = "known_anchors" in inspect.signature(assigner).parameters
        if accepts_known:
            return assigner(
                date_str=date_str,
                today_clusters=today_clusters,
                weekly_anchors=weekly_anchors,
                known_anchors=known,
            )
        return assigner(
            date_str=date_str,
            today_clusters=today_clusters,
            weekly_anchors=weekly_anchors,
        )
    accepts_known = "known_anchors" in inspect.signature(gateway).parameters
    if accepts_known:
        return gateway(
            date_str=date_str,
            today_clusters=today_clusters,
            weekly_anchors=weekly_anchors,
            known_anchors=known,
        )
    return gateway(
        date_str=date_str,
        today_clusters=today_clusters,
        weekly_anchors=weekly_anchors,
    )


def update_anchors_with_assignments(
    weekly_anchors: list[WeeklyAnchor],
    assignment_result: dict[str, Any],
    today_clusters: list[dict],
    date_str: str,
) -> tuple[list[WeeklyAnchor], dict[str, str]]:
    """根据 LLM 输出维护 anchors 表，返回 (新 anchors 列表, cluster_id→final_anchor_slug)。

    final_anchor_slug 为 "" 表示 unanchored。
    """
    by_slug: dict[str, WeeklyAnchor] = {a.slug: a for a in weekly_anchors}
    cluster_by_id: dict[str, dict] = {
        c.get("cluster_id", c.get("id", "")): c for c in today_clusters
    }

    assignments: dict[str, str] = assignment_result.get("assignments", {})
    new_anchors: list[dict] = assignment_result.get("new_anchors", [])
    final_mapping: dict[str, str] = {}

    for new_def in new_anchors:
        slug = new_def.get("slug")
        if not slug:
            continue
        if slug in by_slug:
            existing = by_slug[slug]
            if existing.state == "candidate":
                existing.candidate_for_days += 1
                if existing.candidate_for_days >= _CANDIDATE_PROMOTE_DAYS:
                    existing.state = "active"
            existing.last_active = date_str
        else:
            by_slug[slug] = WeeklyAnchor(
                slug=slug,
                display=new_def.get("display", slug),
                started=new_def.get("started", date_str),
                last_active=date_str,
                state="candidate",
                candidate_for_days=1,
            )

    for cluster_id, target in assignments.items():
        cluster = cluster_by_id.get(cluster_id, {})
        event_count = int(cluster.get("event_count") or 0)
        narrative = cluster.get("narrative_one_line", "")

        if not target or target == "unanchored":
            final_mapping[cluster_id] = ""
            continue

        if target.startswith("new_anchor:"):
            slug = target.split(":", 1)[1]
            anchor = by_slug.get(slug)
            if anchor is None:
                final_mapping[cluster_id] = ""
                continue
            if event_count <= 1 and anchor.state == "candidate":
                final_mapping[cluster_id] = ""
                continue
            anchor.last_active = date_str
            anchor.daily_progress.append({
                "date": date_str,
                "cluster_id": cluster_id,
                "narrative": narrative,
            })
            upsert_anchor_timeline_entry(
                anchor,
                date_str=date_str,
                summary=narrative or anchor.display or anchor.slug,
            )
            final_mapping[cluster_id] = slug
            continue

        anchor = by_slug.get(target)
        if anchor is None:
            final_mapping[cluster_id] = ""
            continue
        anchor.last_active = date_str
        anchor.daily_progress.append({
            "date": date_str,
            "cluster_id": cluster_id,
            "narrative": narrative,
        })
        upsert_anchor_timeline_entry(
            anchor,
            date_str=date_str,
            summary=narrative or anchor.display or anchor.slug,
        )
        if anchor.state == "candidate":
            anchor.candidate_for_days += 1
            if anchor.candidate_for_days >= _CANDIDATE_PROMOTE_DAYS:
                anchor.state = "active"
        final_mapping[cluster_id] = anchor.slug

    today = _parse_date(date_str)
    for anchor in by_slug.values():
        anchor.timeline_entries = _dedupe_timeline_entries(anchor.timeline_entries)
        if anchor.state in ("active", "candidate"):
            last_active = _parse_date(anchor.last_active)
            if today and last_active and (today - last_active) > timedelta(days=_STALE_DAYS):
                anchor.state = "stale"

    return list(by_slug.values()), final_mapping


def _parse_date(s: str) -> date_cls | None:
    try:
        return date_cls.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def split_topics_and_unanchored(
    today_clusters: list[dict],
    final_mapping: dict[str, str],
    weekly_anchors: list[WeeklyAnchor],
) -> tuple[list[dict], list[dict]]:
    """按 mapping 把 clusters 拆成 topics（按 anchor 聚合）+ unanchored。"""
    by_anchor: dict[str, list[dict]] = {}
    unanchored: list[dict] = []
    anchor_lookup = {a.slug: a for a in weekly_anchors}

    for cluster in today_clusters:
        cluster_id = cluster.get("cluster_id", cluster.get("id", ""))
        target = final_mapping.get(cluster_id, "")
        enriched = dict(cluster)
        enriched["anchored_to"] = target or None
        if target:
            by_anchor.setdefault(target, []).append(enriched)
        else:
            unanchored.append(enriched)

    topics: list[dict] = []
    for slug, clusters in by_anchor.items():
        anchor = anchor_lookup.get(slug)
        narratives = [c.get("narrative_one_line", "") for c in clusters if c.get("narrative_one_line")]
        topics.append({
            "anchor": slug,
            "anchor_display": anchor.display if anchor else slug,
            "narrative": " ".join(narratives),
            "decisions": [],
            "shipped": [],
            "events_ref": [c.get("cluster_id", c.get("id", "")) for c in clusters],
        })
    return topics, unanchored


def seed_w19_anchors() -> list[WeeklyAnchor]:
    """W19 (5/4-5/10) 已知主线种子（手工拍板，给首次跑用）。"""
    return [
        WeeklyAnchor(
            slug="weekly-v3-rollout",
            display="周报 v3 设计与落地",
            started="2026-05-06",
            last_active="2026-05-09",
            state="active",
            candidate_for_days=4,
        ),
        WeeklyAnchor(
            slug="hud-ocr-fixes",
            display="HUD / OCR 代码修复",
            started="2026-05-06",
            last_active="2026-05-08",
            state="active",
            candidate_for_days=3,
        ),
        WeeklyAnchor(
            slug="corpusflow-deploy",
            display="CorpusFlow 比赛环境部署",
            started="2026-05-06",
            last_active="2026-05-08",
            state="active",
            candidate_for_days=3,
        ),
    ]
