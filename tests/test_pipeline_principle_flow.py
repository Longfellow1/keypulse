from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from keypulse.obsidian.principle_exporter import export_principles, load_known_principles
from keypulse.pipeline.daily_orchestrator import run_daily
from keypulse.pipeline.daily_summary import write_daily_summary
from keypulse.pipeline.model import ModelBackend
from keypulse.pipeline.principle_distillation import PrincipleDistillationGateway
from keypulse.pipeline.weekly_orchestrator import run_weekly
from keypulse.pipeline import weekly_orchestrator
from keypulse.store.db import close, init_db


BUDGET_DAILY_MARKDOWN = """# 2026-05-01

## 今日要点

你今天把原则提炼链路接进了 daily。

## 今天做的事

### Principle Distillation

你把 keyboard chunk 转成结构化输入，并把提炼结果写进 principles vault。
"""


class FakeModelGateway:
    def __init__(self, response: Any):
        self.response = response
        self.calls: list[tuple[str, Any]] = []

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs):
        self.calls.append((capability, input_data))
        return self.response


class FakeDailyGateway:
    def __init__(self, model: str, responses: dict[str, Any]):
        self.backend = ModelBackend(kind="openai_compatible", base_url="https://example.test/v1", model=model)
        self.responses = responses
        self.calls: list[str] = []

    def select_backend(self, stage: str = "write") -> ModelBackend:
        return self.backend

    def call(self, capability: str, prompt: str, *, input_data: Any = None, **_kwargs):
        self.calls.append(capability)
        response = self.responses[capability]
        if isinstance(response, BaseException):
            raise response
        return response


def _write_model_config(tmp_path: Path, *, cloud_model: str) -> None:
    config_dir = tmp_path / ".keypulse"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.joinpath("config.toml").write_text(
        f"""
[model]
active_profile = "cloud-only"

[model.cloud]
kind = "openai_compatible"
base_url = "https://example.test/v1"
model = "{cloud_model}"
tier = ""

[model.local]
kind = "lm_studio"
base_url = "http://127.0.0.1:1234"
model = "qwen2.5-7b"
tier = ""
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _patch_daily_io(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[dict[str, Any]]) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_topic_anchor._DEFAULT_PATH",
        tmp_path / ".keypulse" / "weekly-anchor.json",
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_rows_for_date", lambda _date: rows)
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._filter_for_trigger", lambda _date, _trigger, loaded: loaded)
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )


def _daily_keyboard_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "source": "keyboard_chunk",
            "speaker": "user",
            "ts_start": "2026-05-01T01:00:00+00:00",
            "ts_end": "2026-05-01T01:01:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "process_name": "Codex Helper",
            "content_text": "先对齐用户心智模型再优化动效，不然每次改版都在返工。",
            "content_hash": "hash-1",
            "metadata_json": json.dumps({"entities": {"session_id": "s1"}}),
            "session_id": "s1",
            "semantic_weight": 1.0,
            "user_present": 1,
        },
        {
            "id": 2,
            "source": "keyboard_chunk",
            "speaker": "user",
            "ts_start": "2026-05-01T01:03:00+00:00",
            "ts_end": "2026-05-01T01:04:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "process_name": "Codex Helper",
            "content_text": "把原则写成可迁移规则，后续在别的项目才能直接复用。",
            "content_hash": "hash-2",
            "metadata_json": json.dumps({"entities": {"session_id": "s1"}}),
            "session_id": "s1",
            "semantic_weight": 1.0,
            "user_present": 1,
        },
        {
            "id": 3,
            "source": "keyboard_chunk",
            "speaker": "user",
            "ts_start": "2026-05-01T01:06:00+00:00",
            "ts_end": "2026-05-01T01:07:00+00:00",
            "app_name": "Codex",
            "window_title": "KeyPulse",
            "process_name": "Codex Helper",
            "content_text": "不要靠触发词硬判定，要看上下文里的决策和取舍。",
            "content_hash": "hash-3",
            "metadata_json": json.dumps({"entities": {"session_id": "s2"}}),
            "session_id": "s2",
            "semantic_weight": 1.0,
            "user_present": 1,
        },
    ]


def _write_topic(path: Path, *, slug: str, display_name: str, first_seen: str, last_seen: str) -> None:
    body = "\n".join(
        [
            "---",
            "type: topic",
            f"slug: {slug}",
            f"display_name: {display_name}",
            f"first_seen: {first_seen}",
            f"last_seen: {last_seen}",
            "keywords:",
            "  - keypulse",
            "  - weekly",
            "  - principle",
            "  - distillation",
            "  - workflow",
            "---",
            "",
            f"# {display_name}",
            "",
            "## Entries",
            "- 2026-04-28 18:00 | 1 events | 上周推进 principle",
            "- 2026-05-04 18:00 | 2 events | 本周推进 principle",
            "",
            "## Related Events",
            "- [[../.keypulse/events/2026-05-04/1|1]]",
            "",
        ]
    )
    path.write_text(body, encoding="utf-8")


def _seed_weekly_inputs(tmp_path: Path) -> None:
    topics_dir = tmp_path / ".keypulse" / "topics"
    topics_dir.mkdir(parents=True, exist_ok=True)
    _write_topic(
        topics_dir / "principle-flow.md",
        slug="principle-flow",
        display_name="Principle Flow",
        first_seen="2026-05-04",
        last_seen="2026-05-10",
    )

    days = [
        "2026-04-27",
        "2026-04-28",
        "2026-04-29",
        "2026-04-30",
        "2026-05-01",
        "2026-05-02",
        "2026-05-03",
    ]
    for idx, day in enumerate(days):
        write_daily_summary(
            day,
            clusters=[
                {
                    "slug": "principle-flow",
                    "display_name": "Principle Flow",
                    "narrative_one_line": f"principle day {idx}",
                    "event_count": 1,
                    "time_range": ["10:00", "10:10"],
                    "merge_candidate_with": [],
                }
            ],
            misc=[],
            topic_snapshot={"principle-flow": "active"},
            cost={"in_tokens": 10, "out_tokens": 10, "cost_usd": 0.0},
        )


def _reset_weekly_runtime_state() -> None:
    weekly_orchestrator._WEEKLY_MEMORY_CACHE.clear()
    weekly_orchestrator._WEEKLY_CIRCUIT.failures = 0
    weekly_orchestrator._WEEKLY_CIRCUIT.open_until = 0.0


def test_principle_distillation_golden_fixture_writes_to_vault(tmp_path: Path) -> None:
    fixture = Path(__file__).resolve().parent / "fixtures" / "principle_distillation" / "ux_mental_model_alignment_chunks.json"
    chunks = json.loads(fixture.read_text(encoding="utf-8"))
    gateway = FakeModelGateway(
        {
            "candidates": [
                {
                    "slug": "ux-mental-model-alignment",
                    "kind": "principle",
                    "distilled": "先让信息结构贴合用户心智模型，再做微交互优化。",
                    "quote": "这次 UX 卡点不是视觉密度，而是用户不知道先看哪里。我们先把信息分层和操作顺序对齐到用户心智模型，再优化文案和动效。",
                    "confidence": 0.89,
                }
            ]
        }
    )

    candidates = PrincipleDistillationGateway(gateway).distill(
        date_str="2026-05-21",
        keyboard_chunks=chunks,
        known_principles=[],
    )
    result = export_principles(
        date_str="2026-05-21",
        candidates=candidates,
        vault_path=tmp_path / "vault",
    )

    assert len(result.written_paths) == 1
    note_path = result.written_paths[0]
    assert note_path.name == "2026-05-21-ux-mental-model-alignment.md"
    content = note_path.read_text(encoding="utf-8")
    assert "principle_id: \"ux-mental-model-alignment\"" in content
    assert "distilled:" in content
    known = load_known_principles(tmp_path / "vault")
    assert known[0]["principle_id"] == "ux-mental-model-alignment"


def test_run_daily_integrates_principle_distillation_and_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_model_config(tmp_path, cloud_model="qwen2.5-7b")
    _patch_daily_io(monkeypatch, tmp_path, _daily_keyboard_rows())
    gateway = FakeDailyGateway(
        "qwen2.5-7b",
        {
            "L1_cluster_review": {
                "clusters": [
                    {"component_id": "c1", "topic_action": "existing", "topic_slug": "principle-flow", "reason": "keep"}
                ],
                "misc_event_ids": [],
            },
            "L2_narrative": {"markdown": BUDGET_DAILY_MARKDOWN},
            "L0_anchor": {"assignments": {"c1": "unanchored"}, "new_anchors": []},
            "L7_principle_distillation": {
                "candidates": [
                    {
                        "slug": "ux-mental-model-alignment",
                        "kind": "principle",
                        "distilled": "先让信息结构贴合用户心智模型，再做微交互优化。",
                        "quote": "先对齐用户心智模型再优化动效，不然每次改版都在返工。把原则写成可迁移规则，后续在别的项目才能直接复用。",
                        "confidence": 0.9,
                    }
                ]
            },
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    summary = run_daily("2026-05-01", trigger="18:00")

    principle_note = tmp_path / "vault" / "principles" / "2026-05-01-ux-mental-model-alignment.md"
    assert principle_note.exists()
    assert "L7_principle_distillation" in gateway.calls

    record = json.loads((tmp_path / ".keypulse" / "run_records" / "2026-05-01.json").read_text(encoding="utf-8"))
    details = record["stage_details"]["principle_distillation"]
    assert details["principle_count"] == "1"
    assert details["errors"] == ""
    assert Path(summary.daily_path).exists()


def test_run_weekly_renders_new_principles_section_for_plain_and_exec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reset_weekly_runtime_state()
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    _seed_weekly_inputs(tmp_path)

    export_principles(
        date_str="2026-04-29",
        candidates=[
            {
                "slug": "ux-mental-model-alignment",
                "kind": "principle",
                "distilled": "先对齐用户心智模型，再打磨交互细节。",
                "quote": "先对齐用户心智模型再优化动效，不然每次改版都在返工。",
                "confidence": 0.88,
            }
        ],
        vault_path=tmp_path / "vault",
    )
    export_principles(
        date_str="2026-05-08",
        candidates=[
            {
                "slug": "outside-week",
                "kind": "principle",
                "distilled": "这个原则不在目标周。",
                "quote": "这一条应当被过滤掉，因为日期不在当前 ISO 周。",
                "confidence": 0.6,
            }
        ],
        vault_path=tmp_path / "vault",
    )

    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("KEYPULSE_WEEKLY_RETRY_SLEEP", "0")

    plain_path = Path(run_weekly("2026-W18", style="plain"))
    exec_path = Path(run_weekly("2026-W18", style="exec"))

    plain = plain_path.read_text(encoding="utf-8")
    exec_body = exec_path.read_text(encoding="utf-8")
    assert "## 本周新沉淀原则" in plain
    assert "- [[ux-mental-model-alignment]] — 先对齐用户心智模型，再打磨交互细节。" in plain
    assert "outside-week" not in plain

    assert "## 本周新沉淀原则" in exec_body
    assert "- [[ux-mental-model-alignment]] — 先对齐用户心智模型，再打磨交互细节。" in exec_body
    assert "outside-week" not in exec_body
    close()
