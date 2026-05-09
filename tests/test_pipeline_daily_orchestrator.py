from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from keypulse.cli import main
from keypulse.pipeline.daily_orchestrator import DailyOrchestratorError, run_daily
from keypulse.pipeline.daily_orchestrator import _event_value_density
from keypulse.pipeline.model import ModelBackend
from keypulse.store.db import close, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


DAILY_MARKDOWN = """📍 Asia/Shanghai

# 2026-05-01

## 今日要点

你今天确认 daily 生成策略需要按模型能力分流，旗舰模型承担整篇生成，预算模型保留结构化聚类交接。这让日报质量和调用成本可以被同一个入口稳定管理。

## 今天做的事

### KeyPulse Daily Strategy

你围绕 KeyPulse daily 管线完成策略拆分，把事件抽取、主题命名和日报写入放回编排层。这个调整让大模型走单次生成，小模型继续依赖聚类结果，边界更清楚。

## 明日的锚点

> 明天我想：______
>
> _写一句话留给明天的自己_
"""


class FakeGateway:
    def __init__(self, model: str, responses: dict[str, Any]):
        self.backend = ModelBackend(kind="openai_compatible", base_url="https://example.test/v1", model=model)
        self.responses = responses
        self.calls: list[str] = []
        self.inputs: list[dict[str, Any]] = []

    def select_backend(self, stage: str = "write") -> ModelBackend:
        return self.backend

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs) -> Any:
        self.calls.append(capability)
        self.inputs.append({"capability": capability, "input_data": input_data, "prompt": prompt})
        response = self.responses[capability]
        if isinstance(response, BaseException):
            raise response
        return response


def _write_config(tmp_path: Path, *, cloud_model: str, cloud_tier: str = "", active_profile: str = "cloud-only") -> None:
    config_dir = tmp_path / ".keypulse"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath("config.toml").write_text(
        f"""
[model]
active_profile = "{active_profile}"

[model.cloud]
kind = "openai_compatible"
base_url = "https://example.test/v1"
model = "{cloud_model}"
tier = "{cloud_tier}"

[model.local]
kind = "lm_studio"
base_url = "http://127.0.0.1:1234"
model = "qwen2.5-7b"
tier = ""
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "source": "ax_text",
            "speaker": "user",
            "ts_start": "2026-05-01T01:00:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "content_text": "KeyPulse daily strategy refactor model tier routing",
            "metadata_json": json.dumps({"entities": {"session_id": "s1", "named_entities": ["KeyPulse"]}}),
        },
        {
            "id": 2,
            "source": "ax_text",
            "speaker": "ai",
            "ts_start": "2026-05-01T01:03:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "content_text": "Budget path should call L1 then one L2 narrative",
            "metadata_json": json.dumps({"entities": {"session_id": "s1", "named_entities": ["KeyPulse"]}}),
        },
        {
            "id": 3,
            "source": "clipboard",
            "speaker": "user",
            "ts_start": "2026-05-01T03:00:00+00:00",
            "app_name": "Chrome",
            "window_title": "Misc",
            "content_text": "misc unrelated browsing",
            "metadata_json": json.dumps({"entities": {"session_id": "s2"}}),
        },
    ]


def _patch_io(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_rows_for_date", lambda _date: rows)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._filter_for_trigger", lambda _date, _trigger, loaded: loaded)
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )


def test_flagship_path_calls_one_llm_and_skips_topics(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="doubao-seed-1-6-250615")
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway("doubao-seed-1-6-250615", {"daily_flagship": {"markdown": DAILY_MARKDOWN}})
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    summary = run_daily("2026-05-01", trigger="18:00")

    assert gateway.calls == ["daily_flagship"]
    assert summary.cluster_count == 0
    assert summary.misc_event_ids == ()
    assert Path(summary.daily_path).read_text(encoding="utf-8") == DAILY_MARKDOWN.strip()
    assert not (tmp_path / ".keypulse" / "hot.md").exists()


def test_budget_path_calls_l1_l2_once_and_l3_for_new_topic(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="qwen2.5-7b")
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway(
        "qwen2.5-7b",
        {
            "L1_cluster_review": {
                "clusters": [
                    {"component_id": "c1", "topic_action": "new", "reason": "new work"},
                    {"component_id": "c2", "topic_action": "misc", "reason": "noise"},
                ],
                "misc_event_ids": ["3"],
            },
            "L2_narrative": {"markdown": DAILY_MARKDOWN},
            "L3_topic_naming": {
                "slug": "keypulse-daily-strategy",
                "display_name": "KeyPulse Daily Strategy",
                "keywords": ["keypulse", "daily", "strategy", "tier", "budget"],
            },
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    summary = run_daily("2026-05-01", trigger="18:00")

    assert gateway.calls == ["L1_cluster_review", "L2_narrative", "L3_topic_naming"]
    assert summary.cluster_count == 1
    assert summary.misc_event_ids == ("3",)
    assert summary.topic_diffs == ("keypulse-daily-strategy:created",)
    assert (tmp_path / ".keypulse" / "hot.md").read_text(encoding="utf-8").count("keypulse-daily-strategy") == 1
    l2_input = next(item["input_data"] for item in gateway.inputs if item["capability"] == "L2_narrative")
    assert len(l2_input["clusters"]) == 1
    assert len(l2_input["misc_events"]) == 1


def test_event_value_density_promotes_user_decisions_over_tool_echo():
    decision_event = {
        "id": "decision",
        "source": "clipboard",
        "speaker": "user",
        "content_text": "我建议选择方案 B，根因是入口职责需要拆开，应该把两个用户路径分开。",
    }
    tool_echo_event = {
        "id": "echo",
        "source": "ax_text",
        "speaker": "system",
        "content_text": "When using Powerlevel10k with instant prompt, console output during zsh initialization may indicate issues. " * 20,
    }

    assert _event_value_density(decision_event) > _event_value_density(tool_echo_event)


def test_tier_auto_recognizes_flagship_model(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="deepseek-chat")
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway("deepseek-chat", {"daily_flagship": {"markdown": DAILY_MARKDOWN}})
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    run_daily("2026-05-01", trigger="18:00")

    assert gateway.calls == ["daily_flagship"]


def test_tier_override_wins_over_model_card(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="deepseek-chat", cloud_tier="budget")
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway(
        "deepseek-chat",
        {
            "L1_cluster_review": {"clusters": [{"component_id": "c1", "topic_action": "existing", "topic_slug": "existing-topic"}], "misc_event_ids": []},
            "L2_narrative": {"markdown": DAILY_MARKDOWN},
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)
    topics_dir = tmp_path / ".keypulse" / "topics"
    topics_dir.mkdir(parents=True)
    topics_dir.joinpath("existing-topic.md").write_text(
        "---\ntype: topic\nslug: existing-topic\ndisplay_name: KeyPulse Daily Strategy\nfirst_seen: 2026-04-30\nlast_seen: 2026-04-30\nkeywords:\n  - keypulse\n  - daily\n  - strategy\n  - tier\n  - budget\n---\n\n# KeyPulse Daily Strategy\n",
        encoding="utf-8",
    )

    run_daily("2026-05-01", trigger="18:00")

    assert gateway.calls == ["L1_cluster_review", "L2_narrative"]


def test_daily_strategy_error_is_translated(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="doubao-seed-1-6-250615")
    _patch_io(monkeypatch, tmp_path, _rows())
    gateway = FakeGateway("doubao-seed-1-6-250615", {"daily_flagship": {"markdown": ""}})
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    with pytest.raises(DailyOrchestratorError, match="empty markdown"):
        run_daily("2026-05-01", trigger="18:00")


def test_low_volume_skip_unchanged(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="doubao-seed-1-6-250615")
    _patch_io(monkeypatch, tmp_path, _rows()[:2])

    summary = run_daily("2026-05-01", trigger="18:00")

    assert summary.skipped is True
    assert summary.cluster_count == 0
    assert summary.misc_event_ids == ("1", "2")
    assert "事件不足 3 条" in Path(summary.daily_path).read_text(encoding="utf-8")


def _insert_event(
    *,
    ts_start: str,
    content: str,
    session_id: str,
    app: str = "Codex",
    window: str = "KeyPulse",
    entities: dict | None = None,
) -> None:
    payload = {"entities": dict(entities or {})}
    payload["entities"]["session_id"] = session_id
    event = RawEvent(
        source="window",
        event_type="window_title_changed",
        ts_start=ts_start,
        app_name=app,
        window_title=window,
        content_text=content,
        metadata_json=json.dumps(payload, ensure_ascii=False),
        session_id=session_id,
    )
    insert_raw_event(event)


def _seed_minimal_events() -> None:
    _insert_event(ts_start="2026-05-01T01:00:00+00:00", content="实现 daily orchestrator 主流程", session_id="s1")
    _insert_event(ts_start="2026-05-01T01:03:00+00:00", content="补 L1 schema 并校验输出", session_id="s1")
    _insert_event(ts_start="2026-05-01T01:20:00+00:00", content="补 L2 narrative prompt", session_id="s2")


def test_daily_cli_falls_back_to_things_when_mock_llm_keeps_failing(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_minimal_events()
    monkeypatch.setenv("MOCK_LLM_FAILS", "99")
    fallback_target = tmp_path / "vault" / "Daily" / "2026-05-01.md"

    def fake_fallback(_cfg, _date_str, *, no_llm):
        fallback_target.parent.mkdir(parents=True, exist_ok=True)
        fallback_target.write_text("# fallback\n\n## 今日概览\n\n- things fallback\n", encoding="utf-8")
        return fallback_target

    monkeypatch.setattr("keypulse.cli._render_daily_fallback_with_things", fake_fallback)

    result = CliRunner().invoke(
        main,
        [
            "daily",
            "run",
            "--date",
            "2026-05-01",
            "--trigger",
            "18:00",
            "--mock-llm",
        ],
    )

    assert result.exit_code == 0
    assert "daily_run=fallback" in result.output
    assert "fallback_daily_path=" in result.output
    assert fallback_target.exists()
    close()
