from __future__ import annotations

from typing import Any

import pytest

from keypulse.pipeline.anchor_gateway import AnchorGateway, AnchorGatewayError
from keypulse.pipeline.weekly_topic_anchor import WeeklyAnchor


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
    assert result["assignments"]["c1"] == "weekly-v3-rollout"


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
