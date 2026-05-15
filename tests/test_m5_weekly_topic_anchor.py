"""M5 weekly_topic_anchor 测试 — 主线状态机 + 归并 + 单 event 防升格。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from keypulse.pipeline.weekly_topic_anchor import (
    WeeklyAnchor,
    anchor_today_clusters,
    load_weekly_anchors,
    save_weekly_anchors,
    seed_w19_anchors,
    split_topics_and_unanchored,
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


def test_load_resets_on_new_week(tmp_path: Path) -> None:
    p = tmp_path / "anchor.json"
    save_weekly_anchors("2026-W19", seed_w19_anchors(), path=p)
    loaded = load_weekly_anchors("2026-W20", path=p)
    assert loaded == [], "新一周应自动 reset"


def test_load_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "no.json"
    assert load_weekly_anchors("2026-W19", path=p) == []


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
