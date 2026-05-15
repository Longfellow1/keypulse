"""本周主线锚定层 — daily 与 weekly 之间的运行时状态。

设计文档: docs/golden-daily/SCHEMA.md
落盘: ~/.keypulse/weekly-anchor.json

核心契约：
- daily 跑前 load_weekly_anchors，跑完 save_weekly_anchors
- anchor_today_clusters 委托 gateway（LLM/mock）一次性归并今日 events → assignments
- update_anchors_with_assignments 维护主线表（candidate→active→stale）
- 单 event 在没显式归到现有主线时一律 unanchored，避免噪音升格
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

AnchorState = Literal["active", "candidate", "completed", "stale"]

_DEFAULT_PATH = Path.home() / ".keypulse" / "weekly-anchor.json"
_CANDIDATE_PROMOTE_DAYS = 2
_STALE_DAYS = 5


@dataclass
class WeeklyAnchor:
    slug: str
    display: str
    started: str
    last_active: str
    state: AnchorState
    daily_progress: list[dict] = field(default_factory=list)
    candidate_for_days: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WeeklyAnchor":
        return cls(
            slug=d["slug"],
            display=d.get("display", ""),
            started=d.get("started", ""),
            last_active=d.get("last_active", d.get("started", "")),
            state=d.get("state", "active"),
            daily_progress=list(d.get("daily_progress", [])),
            candidate_for_days=int(d.get("candidate_for_days", 0)),
        )


class AnchorGateway(Protocol):
    def assign(
        self,
        *,
        date_str: str,
        today_clusters: list[dict],
        weekly_anchors: list[WeeklyAnchor],
    ) -> dict[str, Any]:
        ...


def _state_payload(week_str: str, anchors: list[WeeklyAnchor]) -> dict:
    return {
        "week": week_str,
        "anchors": [a.to_dict() for a in anchors],
        "saved_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }


def load_weekly_anchors(week_str: str, *, path: Path | None = None) -> list[WeeklyAnchor]:
    """读 ~/.keypulse/weekly-anchor.json。week 不一致时返回空（新一周自动 reset）。"""
    p = path or _DEFAULT_PATH
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if payload.get("week") != week_str:
        return []
    return [WeeklyAnchor.from_dict(d) for d in payload.get("anchors", [])]


def save_weekly_anchors(
    week_str: str,
    anchors: list[WeeklyAnchor],
    *,
    path: Path | None = None,
) -> None:
    p = path or _DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_state_payload(week_str, anchors), ensure_ascii=False, indent=2), encoding="utf-8")


def anchor_today_clusters(
    *,
    date_str: str,
    today_clusters: list[dict],
    weekly_anchors: list[WeeklyAnchor],
    gateway: AnchorGateway | Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """委托 gateway 做归并，返回 {assignments, new_anchors}。

    gateway 可以是含 .assign 方法的对象，或 callable。
    """
    if hasattr(gateway, "assign"):
        return gateway.assign(
            date_str=date_str,
            today_clusters=today_clusters,
            weekly_anchors=weekly_anchors,
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
        if anchor.state == "candidate":
            anchor.candidate_for_days += 1
            if anchor.candidate_for_days >= _CANDIDATE_PROMOTE_DAYS:
                anchor.state = "active"
        final_mapping[cluster_id] = anchor.slug

    today = _parse_date(date_str)
    for anchor in by_slug.values():
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
