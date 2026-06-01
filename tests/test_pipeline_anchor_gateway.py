from __future__ import annotations

from typing import Any

import pytest

from keypulse.pipeline.anchor_gateway import AnchorGateway, AnchorGatewayError
from keypulse.pipeline.model import validate_schema_minimal
from keypulse.pipeline.weekly_topic_anchor import WeeklyAnchor
from keypulse.prompts.loader import load_prompt


class FakeModelGateway:
    def __init__(self, response: Any):
        self.response = response
        self.calls: list[tuple[str, Any]] = []

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs):
        self.calls.append((capability, input_data))
        return self.response


def test_anchor_gateway_assign_success():
    gateway = FakeModelGateway(
        {
            "assignments": {"c1": "weekly-v3-rollout"},
            "new_anchors": [],
        }
    )
    anchor_gateway = AnchorGateway(gateway)  # type: ignore[arg-type]

    result = anchor_gateway.assign(
        date_str="2026-05-09",
        today_clusters=[
            {
                "cluster_id": "c1",
                "display_name": "v3",
                "narrative_one_line": "推进",
                "event_count": 3,
            }
        ],
        weekly_anchors=[
            WeeklyAnchor(
                slug="weekly-v3-rollout",
                display="周报 v3 设计与落地",
                started="2026-05-06",
                last_active="2026-05-08",
                state="active",
            )
        ],
    )

    assert gateway.calls[0][0] == "L0_anchor"
    input_data = gateway.calls[0][1]
    assert isinstance(input_data, dict)
    assert "known_anchors" in input_data
    assert input_data["known_anchors"][0]["slug"] == "weekly-v3-rollout"
    assert result["assignments"]["c1"] == "weekly-v3-rollout"


def test_l0_anchor_input_schema_accepts_known_anchors():
    schema = load_prompt("L0_anchor").input_schema
    payload = {
        "date": "2026-05-09",
        "today_clusters": [
            {
                "cluster_id": "c1",
                "display_name": "v3",
                "narrative_one_line": "推进",
                "event_count": 3,
            }
        ],
        "weekly_anchors": [],
        "known_anchors": [
            {
                "slug": "weekly-v3-rollout",
                "display": "周报 v3 设计与落地",
                "started": "2026-05-06",
                "last_active": "2026-05-08",
                "state": "dormant",
                "daily_progress": [],
                "candidate_for_days": 0,
            }
        ],
    }
    validate_schema_minimal(schema, payload, "$")


def test_anchor_gateway_known_anchors_sorted_and_capped():
    gateway = FakeModelGateway({"assignments": {"c1": "a-110"}, "new_anchors": []})
    anchor_gateway = AnchorGateway(gateway)  # type: ignore[arg-type]
    known_anchors: list[WeeklyAnchor] = []
    for idx in range(110):
        day = (idx % 28) + 1
        known_anchors.append(
            WeeklyAnchor(
                slug=f"a-{idx:03d}",
                display=f"A {idx:03d}",
                started=f"2026-04-{day:02d}",
                last_active=f"2026-05-{day:02d}",
                state="stale",
            )
        )

    anchor_gateway.assign(
        date_str="2026-05-30",
        today_clusters=[
            {
                "cluster_id": "c1",
                "display_name": "v3",
                "narrative_one_line": "推进",
                "event_count": 3,
            }
        ],
        weekly_anchors=known_anchors[:5],
        known_anchors=known_anchors,
    )

    payload = gateway.calls[0][1]
    assert isinstance(payload, dict)
    selected = payload["known_anchors"]
    assert len(selected) == 30
    assert selected[0]["slug"] == "a-083"


def test_anchor_gateway_truncates_daily_progress_for_prompt():
    """daily_progress is not read by the L0_anchor prompt; bloated history
    explodes the input from ~5k → ~70k tokens and the LLM returns empty.
    Verify only the most recent entry is kept and narratives are truncated."""
    gateway = FakeModelGateway({"assignments": {"c1": "noisy"}, "new_anchors": []})
    anchor_gateway = AnchorGateway(gateway)  # type: ignore[arg-type]
    long_narrative = "A" * 500
    bloated = WeeklyAnchor(
        slug="noisy",
        display="Noisy",
        started="2026-05-01",
        last_active="2026-05-29",
        state="active",
        daily_progress=[
            {"date": f"2026-05-{i:02d}", "cluster_id": f"topic-{i}", "narrative": long_narrative}
            for i in range(1, 21)
        ],
    )

    anchor_gateway.assign(
        date_str="2026-05-29",
        today_clusters=[
            {
                "cluster_id": "c1",
                "display_name": "x",
                "narrative_one_line": "y",
                "event_count": 1,
            }
        ],
        weekly_anchors=[bloated],
    )

    payload = gateway.calls[0][1]
    dumped_anchor = payload["weekly_anchors"][0]
    progress = dumped_anchor["daily_progress"]
    assert len(progress) == 1
    assert progress[0]["date"] == "2026-05-20"
    assert progress[0]["narrative"].endswith("…")
    assert len(progress[0]["narrative"]) <= 101


def test_anchor_gateway_assign_rejects_invalid_output():
    gateway = FakeModelGateway({"oops": True})
    anchor_gateway = AnchorGateway(gateway)  # type: ignore[arg-type]

    with pytest.raises(AnchorGatewayError, match="output validation failed") as exc:
        anchor_gateway.assign(
            date_str="2026-05-09",
            today_clusters=[
                {
                    "cluster_id": "c1",
                    "display_name": "v3",
                    "narrative_one_line": "推进",
                    "event_count": 3,
                }
            ],
            weekly_anchors=[],
        )

    assert "CAPABILITY: L0_anchor" in exc.value.prompt
    assert exc.value.raw_response == {"oops": True}
