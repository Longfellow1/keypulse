from __future__ import annotations

from keypulse.pipeline.contracts import PipelineInputs, PipelinePlan, PipelineStage, StageBudget
from keypulse.pipeline.write import build_daily_draft


class _FakeGateway:
    active_profile = "local-first"

    def render_daily_narrative(self, work_blocks, prompt_patch: str = "") -> str:
        return f"narrative::{len(work_blocks)}::{prompt_patch}"


def test_build_daily_draft_uses_model_gateway_metadata_when_available():
    draft = build_daily_draft(
        PipelineInputs(event_count=1, candidate_count=0, topic_count=0, active_days=1),
        [
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-18T09:00:00+00:00",
                "title": "Fix install path",
                "body": "install.sh now reuses the venv",
            }
        ],
        model_gateway=_FakeGateway(),
        plan=PipelinePlan(
            write=StageBudget(PipelineStage.WRITE, True, 1, "test"),
            mine=StageBudget(PipelineStage.MINE, False, 0, "test"),
            aggregate=StageBudget(PipelineStage.AGGREGATE, False, 0, "test"),
        ),
    )

    assert draft.llm_used is True
    assert draft.model_name == "local-first"
    assert draft.prompt_hash
    assert "narrative::" in draft.body
