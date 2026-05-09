from __future__ import annotations

from keypulse.pipeline.topic_status import compute_topic_status


def _summary(date: str, clusters: list[dict]) -> dict:
    return {
        "date": date,
        "clusters": clusters,
        "misc_event_ids": [],
        "topic_status_snapshot": {},
        "cost": {"in_tokens": 0, "out_tokens": 0, "cost_usd": 0.0},
    }


def test_topic_status_boundaries_and_acceleration():
    topics_index = [
        {
            "slug": "new-topic",
            "display_name": "New Topic",
            "first_seen": "2026-05-04",
            "last_seen": "2026-05-02",
        },
        {
            "slug": "accelerating-topic",
            "display_name": "Accelerating Topic",
            "first_seen": "2026-03-01",
            "last_seen": "2026-05-04",
        },
        {
            "slug": "declining-topic",
            "display_name": "Declining Topic",
            "first_seen": "2026-03-01",
            "last_seen": "2026-05-03",
        },
        {
            "slug": "revived-topic",
            "display_name": "Revived Topic",
            "first_seen": "2026-01-01",
            "last_seen": "2026-04-10",
        },
        {
            "slug": "ongoing-topic",
            "display_name": "Ongoing Topic",
            "first_seen": "2026-01-01",
            "last_seen": "2026-05-03",
        },
    ]

    week_daily = [
        _summary(
            "2026-05-04",
            [
                {"slug": "new-topic", "display_name": "New Topic", "event_count": 1},
                {"slug": "accelerating-topic", "display_name": "Accelerating Topic", "event_count": 1},
                {"slug": "declining-topic", "display_name": "Declining Topic", "event_count": 1},
                {"slug": "revived-topic", "display_name": "Revived Topic", "event_count": 1},
                {"slug": "ongoing-topic", "display_name": "Ongoing Topic", "event_count": 1},
            ],
        ),
        _summary(
            "2026-05-05",
            [
                {"slug": "accelerating-topic", "display_name": "Accelerating Topic", "event_count": 1},
                {"slug": "ongoing-topic", "display_name": "Ongoing Topic", "event_count": 1},
            ],
        ),
        _summary(
            "2026-05-06",
            [
                {"slug": "accelerating-topic", "display_name": "Accelerating Topic", "event_count": 1},
                {"slug": "ongoing-topic", "display_name": "Ongoing Topic", "event_count": 1},
            ],
        ),
        _summary(
            "2026-05-07",
            [
                {"slug": "ongoing-topic", "display_name": "Ongoing Topic", "event_count": 1},
            ],
        ),
        _summary("2026-05-08", []),
        _summary("2026-05-09", []),
        _summary("2026-05-10", []),
    ]

    weekly_history = {
        "new-topic": [0, 0],
        "accelerating-topic": [0, 1],
        "declining-topic": [3, 3],
        "revived-topic": [0, 0],
        "ongoing-topic": [4, 4, 4],
    }

    status_map = compute_topic_status(
        topics_index=topics_index,
        daily_summaries=week_daily,
        week_str="2026-W19",
        weekly_history=weekly_history,
    )

    assert status_map["new-topic"].status == "new"
    assert status_map["new-topic"].lifecycle_status == "emerging"

    accelerating = status_map["accelerating-topic"]
    assert accelerating.status == "accelerating"
    assert accelerating.week_mentions == 3
    assert accelerating.last_week_mentions == 1
    assert accelerating.prev_week_mentions == 0
    assert accelerating.acceleration_delta == 1
    assert accelerating.acceleration == 6.0
    assert accelerating.acceleration_ratio == 6.0

    declining = status_map["declining-topic"]
    assert declining.status == "declining"
    assert declining.acceleration_delta < 0

    revived = status_map["revived-topic"]
    assert revived.status == "revived"

    ongoing = status_map["ongoing-topic"]
    assert ongoing.status == "ongoing"


def test_topic_status_includes_dormant_topics():
    topics_index = [
        {
            "slug": "dormant-topic",
            "display_name": "Dormant Topic",
            "first_seen": "2026-01-01",
            "last_seen": "2026-04-20",
        }
    ]

    week_daily = [_summary("2026-05-04", []), _summary("2026-05-05", []), _summary("2026-05-06", []), _summary("2026-05-07", []), _summary("2026-05-08", []), _summary("2026-05-09", []), _summary("2026-05-10", [])]

    status_map = compute_topic_status(
        topics_index=topics_index,
        daily_summaries=week_daily,
        week_str="2026-W19",
        weekly_history={"dormant-topic": [2, 2]},
    )

    dormant = status_map["dormant-topic"]
    assert dormant.status == "steady"
    assert dormant.lifecycle_status == "dormant"
    assert dormant.week_mentions == 0


def test_topic_status_uses_event_count_and_steady_boundary():
    topics_index = [
        {
            "slug": "steady-topic",
            "display_name": "Steady Topic",
            "first_seen": "2026-03-01",
            "last_seen": "2026-05-03",
        }
    ]
    week_daily = [
        _summary("2026-05-04", [{"slug": "steady-topic", "display_name": "Steady Topic", "event_count": 2}]),
        _summary("2026-05-05", [{"slug": "steady-topic", "display_name": "Steady Topic", "event_count": 2}]),
        _summary("2026-05-06", [{"slug": "steady-topic", "display_name": "Steady Topic", "event_count": 2}]),
        _summary("2026-05-07", []),
        _summary("2026-05-08", []),
        _summary("2026-05-09", []),
        _summary("2026-05-10", []),
    ]

    status_map = compute_topic_status(
        topics_index=topics_index,
        daily_summaries=week_daily,
        week_str="2026-W19",
        weekly_history={"steady-topic": [7, 5]},
    )

    steady = status_map["steady-topic"]
    assert steady.week_mentions == 6
    assert steady.last_week_mentions == 5
    assert steady.prev_week_mentions == 7
    assert steady.status == "steady"
    assert steady.acceleration_delta == 3
    assert steady.acceleration_ratio == 1.0
