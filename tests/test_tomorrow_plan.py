from __future__ import annotations

from pathlib import Path

import pytest

from keypulse.obsidian.exporter import (
    _read_tomorrow_plan,
    _render_previous_plan_acknowledgment,
    _render_tomorrow_plan_section,
    build_obsidian_bundle,
)
from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.narrative import WorkBlock


def test_read_tomorrow_plan_missing_file_returns_empty(tmp_path: Path):
    assert _read_tomorrow_plan(tmp_path / "missing.md") == ""


@pytest.mark.parametrize("content", ["______", "__", "", "   "])
def test_read_tomorrow_plan_treats_placeholders_as_empty(tmp_path: Path, content: str):
    path = tmp_path / "2026-04-20.md"
    path.write_text(f"# Day\n\n> 明天我想：{content}\n", encoding="utf-8")

    assert _read_tomorrow_plan(path) == ""


def test_read_tomorrow_plan_extracts_real_content(tmp_path: Path):
    path = tmp_path / "2026-04-20.md"
    path.write_text(
        "# Day\n\n> 明天我想：把 KeyPulse M_Q 收尾\n> _写一句话留给明天的自己_\n",
        encoding="utf-8",
    )

    assert _read_tomorrow_plan(path) == "把 KeyPulse M_Q 收尾"


def test_render_tomorrow_plan_section_uses_placeholder_when_empty():
    assert _render_tomorrow_plan_section() == [
        "",
        "## 明天的锚点",
        "",
        "> 明天我想：______",
        "",
        "> _写一句话留给明天的自己_",
    ]


def test_render_tomorrow_plan_section_uses_existing_content():
    assert _render_tomorrow_plan_section("把 KeyPulse M_Q 收尾") == [
        "",
        "## 明天的锚点",
        "",
        "> 明天我想：把 KeyPulse M_Q 收尾",
        "",
        "> _写一句话留给明天的自己_",
    ]


def test_render_previous_plan_acknowledgment_empty_returns_empty_list():
    assert _render_previous_plan_acknowledgment("") == []


def test_render_previous_plan_acknowledgment_returns_callout():
    assert _render_previous_plan_acknowledgment("把 KeyPulse M_Q 收尾") == [
        "",
        "> 💭 昨天你说想：把 KeyPulse M_Q 收尾",
        "",
    ]


def test_build_obsidian_bundle_includes_previous_plan_acknowledgment_in_daily():
    bundle = build_obsidian_bundle(
        [],
        vault_name="Harland Knowledge",
        date_str="2026-04-21",
        previous_plan="把 KeyPulse M_Q 收尾",
    )

    assert "> 💭 昨天你说想：把 KeyPulse M_Q 收尾" in bundle["daily"][0]["body"]


def test_build_obsidian_bundle_omits_previous_plan_acknowledgment_when_empty():
    bundle = build_obsidian_bundle([], vault_name="Harland Knowledge", date_str="2026-04-21")

    assert "> 💭 昨天你说想：" not in bundle["daily"][0]["body"]


def test_build_obsidian_bundle_renders_tomorrow_anchor_and_preserves_existing_plan():
    bundle = build_obsidian_bundle(
        [],
        vault_name="Harland Knowledge",
        date_str="2026-04-21",
        current_plan_existing="把 KeyPulse M_Q 收尾",
    )

    daily_body = bundle["daily"][0]["body"]
    assert "## 明天的锚点" in daily_body
    assert "> 明天我想：把 KeyPulse M_Q 收尾" in daily_body
    assert "> 明天我想：______" not in daily_body


def test_build_obsidian_bundle_renders_tomorrow_anchor_placeholder_when_empty():
    bundle = build_obsidian_bundle([], vault_name="Harland Knowledge", date_str="2026-04-21")

    daily_body = bundle["daily"][0]["body"]
    assert "## 明天的锚点" in daily_body
    assert "> 明天我想：______" in daily_body


class _FakeBackend:
    kind = "openai_compatible"
    base_url = "https://example.com"
    model = "gpt-test"

    def is_disabled(self) -> bool:
        return False


def test_render_daily_narrative_includes_user_intent_in_prompt(monkeypatch):
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
    blocks = [
        WorkBlock(
            theme="研究",
            duration_sec=600,
            ts_start="2026-04-20T09:00:00+00:00",
            ts_end="2026-04-20T09:10:00+00:00",
            primary_app="Safari",
            event_count=1,
            key_candidates=[],
            user_candidates=[
                {"title": "note", "source": "keyboard_chunk", "created_at": "2026-04-20T09:00:00+00:00"}
            ],
            system_candidates=[
                {"title": "tab", "source": "window", "created_at": "2026-04-20T09:05:00+00:00"}
            ],
            continuity="new",
        )
    ]

    body = gateway.render_daily_narrative(
        blocks,
        prompt_patch="profile=local-first",
        user_intent="把 KeyPulse M_Q 收尾",
    )

    assert body == "LLM output"
    assert "把 KeyPulse M_Q 收尾" in captured["prompt"]
    assert "profile=local-first" in captured["prompt"]
    assert "user_candidates" in captured["prompt"]
    assert "system_candidates" in captured["prompt"]
    assert "叙述结构必须二元" in captured["prompt"]
    assert "user_candidates 为空" in captured["prompt"]
    assert captured["prompt_patch"] == "profile=local-first"
