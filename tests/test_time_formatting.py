from __future__ import annotations

from zoneinfo import ZoneInfo

from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.narrative import WorkBlock, render_daily_narrative


class _FakeBackend:
    kind = "openai_compatible"
    base_url = "https://example.com"
    model = "gpt-test"

    def is_disabled(self) -> bool:
        return False


def _block(*, ts_start: str, ts_end: str) -> WorkBlock:
    return WorkBlock(
        theme="研究",
        duration_sec=600,
        ts_start=ts_start,
        ts_end=ts_end,
        primary_app="Safari",
        event_count=1,
        key_candidates=[],
        continuity="new",
    )


def test_render_daily_narrative_formats_same_day_range_in_local_time(monkeypatch):
    monkeypatch.setattr("keypulse.pipeline.narrative.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))

    body = render_daily_narrative(
        [
            _block(
                ts_start="2026-04-20T10:23:00+00:00",
                ts_end="2026-04-20T10:25:00+00:00",
            )
        ],
        include_heading=False,
    )

    assert "2026年4月20日 18:23–18:25" in body


def test_render_daily_narrative_formats_cross_day_range_in_local_time(monkeypatch):
    monkeypatch.setattr("keypulse.pipeline.narrative.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))

    body = render_daily_narrative(
        [
            _block(
                ts_start="2026-04-20T15:55:00+00:00",
                ts_end="2026-04-20T16:15:00+00:00",
            )
        ],
        include_heading=False,
    )

    assert "2026年4月20日 23:55–2026年4月21日 00:15" in body


def test_model_gateway_prompt_uses_localized_time_strings(monkeypatch):
    monkeypatch.setattr("keypulse.pipeline.narrative.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))

    captured: dict[str, str] = {}

    def fake_select_backend(self, stage: str = "write"):
        return _FakeBackend()

    def fake_call_backend(self, backend, prompt: str, prompt_patch: str = ""):
        captured["prompt"] = prompt
        captured["prompt_patch"] = prompt_patch
        return "LLM output"

    monkeypatch.setattr(ModelGateway, "select_backend", fake_select_backend)
    monkeypatch.setattr(ModelGateway, "_call_backend", fake_call_backend)

    gateway = ModelGateway.__new__(ModelGateway)
    body = gateway.render_daily_narrative(
        [
            _block(
                ts_start="2026-04-20T10:23:00+00:00",
                ts_end="2026-04-20T10:25:00+00:00",
            )
        ],
        prompt_patch="profile=local-first",
    )

    assert body == "LLM output"
    assert "2026年4月20日 18:23" in captured["prompt"]
    assert "2026-04-20T10:23:00+00:00" not in captured["prompt"]
    assert "profile=local-first" in captured["prompt_patch"]
