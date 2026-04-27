from __future__ import annotations

from pathlib import Path

from keypulse.pipeline import write as write_module
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


def test_build_daily_draft_prefers_skeleton_when_enabled(monkeypatch):
    called = {"skeleton": 0, "v2": 0}

    def fake_skeleton_report(db_path, date_str, model_gateway, **kwargs):
        called["skeleton"] += 1
        return "# 2026-04-18 骨架报告\n\n## 今日主线\n- skeleton"

    def fake_v2_narrative(*args, **kwargs):
        called["v2"] += 1
        raise AssertionError("v2 should not be called when skeleton succeeds")

    monkeypatch.setattr(write_module, "build_daily_skeleton_report", fake_skeleton_report)
    monkeypatch.setattr(write_module, "render_v2_narrative", fake_v2_narrative)

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
        use_narrative_skeleton=True,
        db_path=Path("/tmp/keypulse-test.db"),
        date_str="2026-04-18",
    )

    assert called["skeleton"] == 1
    assert called["v2"] == 0
    assert "骨架报告" in draft.body
    assert "## 需要你决定" in draft.body
    assert "## 今天的事件卡" in draft.body


def test_build_daily_draft_falls_back_to_v2_without_event_cards_when_skeleton_fails(monkeypatch):
    def fake_skeleton_report(db_path, date_str, model_gateway, **kwargs):
        raise RuntimeError("boom")

    def fake_v2_narrative(*args, **kwargs):
        return "v2-body"

    monkeypatch.setattr(write_module, "build_daily_skeleton_report", fake_skeleton_report)
    monkeypatch.setattr(write_module, "render_v2_narrative", fake_v2_narrative)

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
        use_narrative_skeleton=True,
        db_path=Path("/tmp/keypulse-test.db"),
        date_str="2026-04-18",
    )

    assert "v2-body" in draft.body
    assert "## 今天的事件卡" not in draft.body
