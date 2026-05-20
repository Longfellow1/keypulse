"""M5 weekly_topic_anchor 测试 — 主线状态机 + 归并 + 单 event 防升格。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keypulse.pipeline.weekly_topic_anchor import (
    WeeklyAnchor,
    anchor_today_clusters,
    load_weekly_anchors,
    migrate_legacy_weekly_anchor_file,
    save_weekly_anchors,
    seed_w19_anchors,
    split_topics_and_unanchored,
    upsert_anchor_timeline_entry,
    update_anchors_with_assignments,
)


class FakeGateway:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def assign(self, *, date_str, today_clusters, weekly_anchors):
        self.calls.append({
            "date_str": date_str,
            "n_clusters": len(today_clusters),
            "n_anchors": len(weekly_anchors),
        })
        return self.result


def test_seed_w19_anchors() -> None:
    anchors = seed_w19_anchors()
    assert len(anchors) == 3
    slugs = {a.slug for a in anchors}
    assert slugs == {"weekly-v3-rollout", "hud-ocr-fixes", "corpusflow-deploy"}
    assert all(a.state == "active" for a in anchors)


def test_load_save_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "anchor.json"
    anchors = seed_w19_anchors()
    save_weekly_anchors("2026-W19", anchors, path=p)
    loaded = load_weekly_anchors("2026-W19", path=p)
    assert len(loaded) == 3
    assert {a.slug for a in loaded} == {a.slug for a in anchors}


def test_load_keeps_cross_week_continuity(tmp_path: Path) -> None:
    p = tmp_path / "anchor.json"
    save_weekly_anchors("2026-W19", seed_w19_anchors(), path=p)
    loaded = load_weekly_anchors("2026-W20", path=p)
    assert len(loaded) == 3, "跨周同 slug 应延续，不应 reset"


def test_load_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "no.json"
    assert load_weekly_anchors("2026-W19", path=p) == []


def test_legacy_file_migrates_without_deleting_source(tmp_path: Path) -> None:
    legacy = tmp_path / "weekly-anchor.json"
    target = tmp_path / "anchors.json"
    legacy.write_text(
        json.dumps(
            {
                "week": "2026-W19",
                "anchors": [
                    {
                        "slug": "v3-rollout",
                        "display": "V3 上线",
                        "started": "2026-05-12",
                        "last_active": "2026-05-15",
                        "state": "active",
                        "daily_progress": [
                            {"date": "2026-05-12", "cluster_id": "c1", "narrative": "立项"},
                            {"date": "2026-05-12", "cluster_id": "c2", "narrative": "立项修订"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    migrated = migrate_legacy_weekly_anchor_file(legacy_path=legacy, target_path=target)
    assert migrated is True
    assert legacy.exists(), "旧文件必须保留作兜底"
    loaded = load_weekly_anchors("2026-W20", path=target)
    assert len(loaded) == 1
    row = loaded[0]
    assert row.slug == "v3-rollout"
    assert row.timeline_entries == [
        {"date": "2026-05-12", "summary": "立项修订", "daily_ref": "[[2026-05-12]]"}
    ]


def test_timeline_entries_roundtrip_and_idempotent_dedupe(tmp_path: Path) -> None:
    p = tmp_path / "anchors.json"
    anchor = WeeklyAnchor(
        slug="v3-rollout",
        display="V3 上线",
        started="2026-05-12",
        last_active="2026-05-20",
        state="active",
        timeline_entries=[
            {"date": "2026-05-12", "summary": "立项", "daily_ref": "[[2026-05-12]]"},
        ],
    )
    upsert_anchor_timeline_entry(anchor, date_str="2026-05-12", summary="立项修订", daily_ref="[[2026-05-12]]")
    upsert_anchor_timeline_entry(anchor, date_str="2026-05-15", summary="跑通 smoke50", daily_ref="[[2026-05-15]]")
    upsert_anchor_timeline_entry(anchor, date_str="2026-05-15", summary="跑通 smoke50", daily_ref="[[2026-05-15]]")
    assert anchor.timeline_entries == [
        {"date": "2026-05-12", "summary": "立项修订", "daily_ref": "[[2026-05-12]]"},
        {"date": "2026-05-15", "summary": "跑通 smoke50", "daily_ref": "[[2026-05-15]]"},
    ]

    anchor.derived_from = "v2-stable"
    save_weekly_anchors("2026-W20", [anchor], path=p)
    loaded = load_weekly_anchors("2026-W21", path=p)
    assert loaded[0].derived_from == "v2-stable"
    assert loaded[0].timeline_entries == anchor.timeline_entries


def test_anchor_to_existing_active() -> None:
    anchors = seed_w19_anchors()
    today_clusters = [
        {"cluster_id": "c1", "narrative_one_line": "M0-M3 落地", "event_count": 5}
    ]
    gateway = FakeGateway({
        "assignments": {"c1": "weekly-v3-rollout"},
        "new_anchors": [],
    })
    raw = anchor_today_clusters(
        date_str="2026-05-09",
        today_clusters=today_clusters,
        weekly_anchors=anchors,
        gateway=gateway,
    )
    new_anchors, mapping = update_anchors_with_assignments(
        anchors, raw, today_clusters, "2026-05-09",
    )
    assert mapping["c1"] == "weekly-v3-rollout"
    target = next(a for a in new_anchors if a.slug == "weekly-v3-rollout")
    assert target.last_active == "2026-05-09"
    assert any(p["cluster_id"] == "c1" for p in target.daily_progress)


def test_new_anchor_starts_as_candidate() -> None:
    anchors = []
    today_clusters = [
        {"cluster_id": "c1", "narrative_one_line": "新主题真活了", "event_count": 4}
    ]
    raw = {
        "assignments": {"c1": "new_anchor:fresh-topic"},
        "new_anchors": [{"slug": "fresh-topic", "display": "新主题", "started": "2026-05-09"}],
    }
    new_anchors, mapping = update_anchors_with_assignments(
        anchors, raw, today_clusters, "2026-05-09",
    )
    fresh = next(a for a in new_anchors if a.slug == "fresh-topic")
    assert fresh.state == "candidate"
    assert fresh.candidate_for_days == 1
    assert mapping["c1"] == "fresh-topic"


def test_candidate_promotes_to_active_after_two_days() -> None:
    anchors = [WeeklyAnchor(
        slug="fresh-topic", display="新主题",
        started="2026-05-08", last_active="2026-05-08",
        state="candidate", candidate_for_days=1,
    )]
    today_clusters = [
        {"cluster_id": "c2", "narrative_one_line": "继续推进", "event_count": 3}
    ]
    raw = {
        "assignments": {"c2": "fresh-topic"},
        "new_anchors": [],
    }
    new_anchors, _ = update_anchors_with_assignments(
        anchors, raw, today_clusters, "2026-05-09",
    )
    fresh = next(a for a in new_anchors if a.slug == "fresh-topic")
    assert fresh.state == "active"
    assert fresh.candidate_for_days >= 2


def test_single_event_new_anchor_demoted_to_unanchored() -> None:
    anchors = []
    today_clusters = [
        {"cluster_id": "c-noise", "narrative_one_line": "登录 SnapDeploy", "event_count": 1}
    ]
    raw = {
        "assignments": {"c-noise": "new_anchor:snapdeploy"},
        "new_anchors": [{"slug": "snapdeploy", "display": "SnapDeploy", "started": "2026-05-09"}],
    }
    new_anchors, mapping = update_anchors_with_assignments(
        anchors, raw, today_clusters, "2026-05-09",
    )
    assert mapping["c-noise"] == "", "单 event 起新 anchor 必须降为 unanchored"


def test_unanchored_target_passes_through() -> None:
    anchors = seed_w19_anchors()
    today_clusters = [
        {"cluster_id": "c-mail", "narrative_one_line": "邮件提醒", "event_count": 1}
    ]
    raw = {"assignments": {"c-mail": "unanchored"}, "new_anchors": []}
    _, mapping = update_anchors_with_assignments(
        anchors, raw, today_clusters, "2026-05-09",
    )
    assert mapping["c-mail"] == ""


def test_stale_after_5_days() -> None:
    anchors = [WeeklyAnchor(
        slug="old-topic", display="老主题",
        started="2026-05-01", last_active="2026-05-01",
        state="active",
    )]
    raw = {"assignments": {}, "new_anchors": []}
    new_anchors, _ = update_anchors_with_assignments(
        anchors, raw, [], "2026-05-09",
    )
    old = next(a for a in new_anchors if a.slug == "old-topic")
    assert old.state == "stale"


def test_split_topics_and_unanchored() -> None:
    anchors = seed_w19_anchors()
    today_clusters = [
        {"cluster_id": "c1", "narrative_one_line": "M0-M3 落地", "event_count": 5},
        {"cluster_id": "c2", "narrative_one_line": "邮件提醒", "event_count": 1},
        {"cluster_id": "c3", "narrative_one_line": "HUD 修复", "event_count": 3},
    ]
    mapping = {"c1": "weekly-v3-rollout", "c2": "", "c3": "hud-ocr-fixes"}
    topics, unanchored = split_topics_and_unanchored(today_clusters, mapping, anchors)
    assert len(topics) == 2
    assert len(unanchored) == 1
    assert unanchored[0]["cluster_id"] == "c2"
    assert unanchored[0]["anchored_to"] is None
    v3 = next(t for t in topics if t["anchor"] == "weekly-v3-rollout")
    assert v3["anchor_display"] == "周报 v3 设计与落地"
    assert "c1" in v3["events_ref"]


def test_gateway_callable_form() -> None:
    """gateway 可以直接是 callable 不必是 protocol 对象。"""
    anchors = seed_w19_anchors()
    today_clusters = [{"cluster_id": "c1", "narrative_one_line": "x", "event_count": 3}]
    captured = {}

    def cb(*, date_str, today_clusters, weekly_anchors):
        captured["called"] = True
        return {"assignments": {"c1": "weekly-v3-rollout"}, "new_anchors": []}

    raw = anchor_today_clusters(
        date_str="2026-05-09",
        today_clusters=today_clusters,
        weekly_anchors=anchors,
        gateway=cb,
    )
    assert captured["called"]
    assert raw["assignments"]["c1"] == "weekly-v3-rollout"
