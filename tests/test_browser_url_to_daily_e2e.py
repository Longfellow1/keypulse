from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from keypulse.pipeline.daily_orchestrator import run_daily
from keypulse.pipeline.model import ModelBackend
from keypulse.store.db import close, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


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


def _write_config(tmp_path: Path, *, cloud_model: str) -> None:
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


def _patch_io(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "keypulse.pipeline.weekly_topic_anchor._DEFAULT_PATH",
        tmp_path / ".keypulse" / "weekly-anchor.json",
    )
    monkeypatch.setattr(
        "keypulse.pipeline.daily_orchestrator.resolve_active_sink",
        lambda _cfg, persist=False: SimpleNamespace(output_dir=tmp_path / "vault"),
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator.local_timezone", lambda: ZoneInfo("Asia/Shanghai"))


def _insert_browser_url_event(
    *,
    url: str,
    title: str,
    content_text: str,
    ts_start: str,
    session_id: str,
) -> None:
    insert_raw_event(
        RawEvent(
            source="browser_url",
            event_type="browser_url_capture",
            ts_start=ts_start,
            app_name="Safari",
            window_title=f"{title} - Safari",
            content_text=content_text,
            content_hash=f"hash-{session_id}",
            metadata_json=json.dumps({"entities": {"urls": [url]}, "browser": "safari", "url": url}, ensure_ascii=False),
            session_id=session_id,
        )
    )


def _insert_keyboard_event(*, ts_start: str, content_text: str, session_id: str, window_title: str) -> None:
    insert_raw_event(
        RawEvent(
            source="keyboard_chunk",
            event_type="keyboard_chunk_capture",
            ts_start=ts_start,
            app_name="Codex",
            window_title=window_title,
            content_text=content_text,
            content_hash=f"hash-{session_id}",
            metadata_json=json.dumps({"entities": {"session_id": session_id}}, ensure_ascii=False),
            session_id=session_id,
        )
    )


def test_browser_url_survives_budget_cluster_ranking_and_reaches_daily_prompt(tmp_path, monkeypatch):
    _write_config(tmp_path, cloud_model="qwen2.5-7b")
    _patch_io(monkeypatch, tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")

    _insert_keyboard_event(
        ts_start="2026-05-01T01:00:00+00:00",
        content_text="typed alpha",
        session_id="keyboard-1",
        window_title="KeyPulse notes",
    )
    _insert_keyboard_event(
        ts_start="2026-05-01T01:02:00+00:00",
        content_text="typed beta",
        session_id="keyboard-1",
        window_title="KeyPulse notes",
    )
    target_url = "https://example.com/docs/target"
    _insert_browser_url_event(
        url=target_url,
        title="OpenAI Docs target",
        content_text="browser visit target",
        ts_start="2026-05-01T07:00:00+00:00",
        session_id="target",
    )

    gateway = FakeGateway(
        "qwen2.5-7b",
        {
            "L1_cluster_review": {
                "clusters": [
                    {"component_id": "c1", "topic_action": "existing", "topic_slug": "keyboard-notes", "reason": "keep"},
                    {"component_id": "c2", "topic_action": "existing", "topic_slug": "browser-url-notes", "reason": "keep"},
                ],
                "misc_event_ids": [],
            },
            "L2_narrative": {"markdown": "# 2026-05-01\n\n## 今日要点\n\n- browser_url seen\n\n## 今天做的事\n\n### 浏览器足迹\n\n看到了浏览器 URL 线索。\n"},
            "L0_anchor": {"assignments": {"c1": "unanchored", "c2": "unanchored"}, "new_anchors": []},
        },
    )
    monkeypatch.setattr("keypulse.pipeline.daily_orchestrator._load_gateway", lambda: gateway)

    summary = run_daily("2026-05-01", trigger="18:00")

    l2_input = next(item["input_data"] for item in gateway.inputs if item["capability"] == "L2_narrative")
    browser_cluster = next(
        cluster for cluster in l2_input["clusters"] if any(event.get("url") == target_url for event in cluster["events"])
    )
    assert summary.skipped is False
    assert l2_input["clusters"][0]["component_id"] == browser_cluster["component_id"]
    assert browser_cluster["events"][0]["url"] == target_url
    assert browser_cluster["events"][0]["win"] == "OpenAI Docs target - Safari"
    close()
