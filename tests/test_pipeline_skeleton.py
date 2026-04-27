from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from keypulse.pipeline.skeleton import build_daily_skeleton_report, build_skeleton_prompt, refresh_daily_skeleton, render_daily_skeleton_report
from keypulse.store.db import init_db


def _insert_hourly_summary(db_path: Path, *, hour: int, payload: dict) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO hourly_summaries(date, hour, payload_json, generated_at) VALUES (?, ?, ?, ?)",
        ("2026-04-25", hour, json.dumps(payload, ensure_ascii=False), "2026-04-25T09:30:00+00:00"),
    )
    conn.commit()
    conn.close()


class _FakeGateway:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def render(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def test_build_skeleton_prompt_includes_prior_and_hourly_context():
    prompt = build_skeleton_prompt(
        "2026-04-25",
        [
            {
                "date": "2026-04-25",
                "hour": 9,
                "payload": {
                    "summary_zh": "在研究设计并修改管线代码",
                    "motive_hints": {"create": 0.5, "maintain": 0.7},
                    "evidence_refs": [1, 2],
                },
            }
        ],
        {
            "date": "2026-04-25",
            "motives": [{"id": "create", "confidence": 0.3, "summary": "old"}],
        },
    )

    assert "上次骨架" in prompt
    assert "在研究设计并修改管线代码" in prompt
    assert len(prompt) < 15000


def test_refresh_daily_skeleton_uses_prior_and_persists(tmp_path):
    db_path = tmp_path / "keypulse.db"
    init_db(db_path)
    _insert_hourly_summary(
        db_path,
        hour=9,
        payload={
            "date": "2026-04-25",
            "hour": 9,
            "topics": ["design"],
            "tools": ["Chrome"],
            "people": [],
            "motive_hints": {
                "create": 0.4,
                "understand": 0.7,
                "decide": 0.6,
                "maintain": 0.2,
                "communicate": 0.0,
                "transact": 0.0,
                "leisure": 0.0,
            },
            "evidence_refs": [1],
            "summary_zh": "在推 narrative 接线方案",
        },
    )

    gateway = _FakeGateway(
        json.dumps(
            {
                "date": "2026-04-25",
                "motives": [
                    {"id": "create", "confidence": 0.2, "summary": "继续做代码落地", "evidence_refs": [1], "gap": ""},
                    {"id": "understand", "confidence": 0.8, "summary": "持续理解设计和实现", "evidence_refs": [1], "gap": "还缺代码提交"},
                    {"id": "decide", "confidence": 0.9, "summary": "在方案间拍板", "evidence_refs": [1], "gap": ""},
                    {"id": "maintain", "confidence": 0.1, "summary": "", "evidence_refs": [], "gap": ""},
                    {"id": "communicate", "confidence": 0.0, "summary": "", "evidence_refs": [], "gap": ""},
                    {"id": "transact", "confidence": 0.0, "summary": "", "evidence_refs": [], "gap": ""},
                    {"id": "leisure", "confidence": 0.0, "summary": "", "evidence_refs": [], "gap": ""},
                ],
                "main_lines": ["你今天主要在做方案选择和代码落地。"],
            }
        )
    )

    payload = refresh_daily_skeleton(db_path, "2026-04-25", gateway)
    assert payload["main_lines"][0].startswith("你今天主要")
    assert gateway.prompts and "上次骨架" in gateway.prompts[0]

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT payload_json FROM daily_skeletons WHERE date=?",
        ("2026-04-25",),
    ).fetchone()
    conn.close()
    assert row is not None

    report = render_daily_skeleton_report("2026-04-25", payload, hourly_summaries=[{"hour": 9, "payload": {"summary_zh": "在推 narrative 接线方案", "evidence_refs": [1]}}])
    assert "## 今日主线" in report
    assert "## 动机骨架" in report
    assert "理解" in report


def test_build_daily_skeleton_report_returns_markdown(tmp_path):
    db_path = tmp_path / "keypulse.db"
    init_db(db_path)
    _insert_hourly_summary(
        db_path,
        hour=9,
        payload={
            "date": "2026-04-25",
            "hour": 9,
            "topics": ["design"],
            "tools": ["Chrome"],
            "people": [],
            "motive_hints": {
                "create": 0.3,
                "understand": 0.7,
                "decide": 0.6,
                "maintain": 0.2,
                "communicate": 0.0,
                "transact": 0.0,
                "leisure": 0.0,
            },
            "evidence_refs": [1],
            "summary_zh": "在推 narrative 接线方案",
        },
    )

    gateway = _FakeGateway(
        json.dumps(
            {
                "date": "2026-04-25",
                "motives": [
                    {"id": "create", "confidence": 0.2, "summary": "继续做代码落地", "evidence_refs": [1], "gap": ""},
                    {"id": "understand", "confidence": 0.8, "summary": "持续理解设计和实现", "evidence_refs": [1], "gap": "还缺代码提交"},
                    {"id": "decide", "confidence": 0.9, "summary": "在方案间拍板", "evidence_refs": [1], "gap": ""},
                    {"id": "maintain", "confidence": 0.1, "summary": "", "evidence_refs": [], "gap": ""},
                    {"id": "communicate", "confidence": 0.0, "summary": "", "evidence_refs": [], "gap": ""},
                    {"id": "transact", "confidence": 0.0, "summary": "", "evidence_refs": [], "gap": ""},
                    {"id": "leisure", "confidence": 0.0, "summary": "", "evidence_refs": [], "gap": ""},
                ],
                "main_lines": ["你今天主要在做方案选择和代码落地。"],
            }
        )
    )

    report = build_daily_skeleton_report(db_path, "2026-04-25", gateway)
    assert report.startswith("# 2026-04-25 骨架报告")
    assert "你今天主要在做方案选择和代码落地" in report
