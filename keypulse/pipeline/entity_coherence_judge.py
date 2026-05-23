"""Entity Coherence Judge — LLM-based eval harness for daily entity pollution detection.

设计文档: M1-C Step 1 — eval harness layer
测试数据: docs/entity-coherence-baseline-2026-05.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path
from typing import Any

from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.daily_strategy import build_prompt
from keypulse.prompts.loader import load_prompt


@dataclass
class TopicJudgement:
    topic_display: str
    is_polluted: bool
    confidence: float
    primary_entity: str
    intruder_entities: list[str]
    polluted_sentences: list[str]
    reasoning: str


@dataclass
class EntityCoherenceReport:
    date: str
    topics_judged: list[TopicJudgement]
    overall_pollution_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "topics_judged": [
                {
                    "topic_display": t.topic_display,
                    "is_polluted": t.is_polluted,
                    "confidence": t.confidence,
                    "primary_entity": t.primary_entity,
                    "intruder_entities": t.intruder_entities,
                    "polluted_sentences": t.polluted_sentences,
                    "reasoning": t.reasoning,
                }
                for t in self.topics_judged
            ],
            "overall_pollution_score": self.overall_pollution_score,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EntityCoherenceReport:
        topics_raw = d.get("topics_judged") or []
        topics = [
            TopicJudgement(
                topic_display=t.get("topic_display", ""),
                is_polluted=bool(t.get("is_polluted", False)),
                confidence=float(t.get("confidence", 0.0)),
                primary_entity=str(t.get("primary_entity", "")),
                intruder_entities=list(t.get("intruder_entities") or []),
                polluted_sentences=list(t.get("polluted_sentences") or []),
                reasoning=str(t.get("reasoning", "")),
            )
            for t in topics_raw
            if isinstance(t, dict)
        ]
        return cls(
            date=str(d.get("date", "")),
            topics_judged=topics,
            overall_pollution_score=float(d.get("overall_pollution_score", 0.0)),
        )


@dataclass
class BaselineReport:
    start_date: str
    end_date: str
    reports: list[EntityCoherenceReport] = field(default_factory=list)

    @property
    def total_days(self) -> int:
        return len(self.reports)

    @property
    def average_pollution_score(self) -> float:
        if not self.reports:
            return 0.0
        return sum(r.overall_pollution_score for r in self.reports) / len(self.reports)

    @property
    def high_pollution_days(self) -> list[EntityCoherenceReport]:
        """返回污染分 > 0.5 的日报"""
        return [r for r in self.reports if r.overall_pollution_score > 0.5]

    @property
    def medium_pollution_days(self) -> list[EntityCoherenceReport]:
        """返回污染分 0.3-0.5 的日报"""
        return [r for r in self.reports if 0.3 < r.overall_pollution_score <= 0.5]

    @property
    def low_pollution_days(self) -> list[EntityCoherenceReport]:
        """返回污染分 0-0.3 的日报"""
        return [r for r in self.reports if 0 < r.overall_pollution_score <= 0.3]

    @property
    def clean_days(self) -> list[EntityCoherenceReport]:
        """返回污染分 = 0 的日报"""
        return [r for r in self.reports if r.overall_pollution_score == 0]


class EntityCoherenceJudge:
    def __init__(self, model_gateway: ModelGateway):
        self._model_gateway = model_gateway

    def judge_daily(
        self,
        date: str,
        daily_markdown: str,
        topics: list[dict[str, str]],
    ) -> EntityCoherenceReport:
        """评测单日的实体污染情况。

        Args:
            date: ISO 8601 日期 (YYYY-MM-DD)
            daily_markdown: vault 渲染的完整日报 markdown
            topics: 包含 topic_display 和 narrative 的列表

        Returns:
            EntityCoherenceReport 对象
        """
        spec = load_prompt("L8_entity_coherence_judge")
        input_data = {
            "date": date,
            "daily_markdown": daily_markdown,
            "topics": [
                {
                    "topic_display": str(t.get("topic_display") or "").strip(),
                    "narrative": str(t.get("narrative") or "").strip(),
                }
                for t in topics
                if str(t.get("topic_display") or "").strip()
                and str(t.get("narrative") or "").strip()
            ],
        }

        prompt = build_prompt(spec.body, "L8_entity_coherence_judge", input_data)

        raw_response: Any = None
        try:
            raw_response = self._model_gateway.call(
                "L8_entity_coherence_judge",
                prompt,
                input_data=input_data,
            )
        except Exception as exc:
            raise RuntimeError(
                f"L8_entity_coherence_judge call failed for {date}: {exc}"
            ) from exc

        if not isinstance(raw_response, dict):
            raise ValueError(
                f"L8_entity_coherence_judge returned non-dict for {date}: {type(raw_response)}"
            )

        # Convert raw response to EntityCoherenceReport
        return EntityCoherenceReport.from_dict(raw_response)

    def run_baseline(
        self,
        start_date: str,
        end_date: str,
        *,
        vault_daily_dir: str | Path | None = None,
        json_daily_dir: str | Path | None = None,
    ) -> BaselineReport:
        """批量跑日期区间内的评测。

        Args:
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            vault_daily_dir: vault 日报目录（默认 ~/Go/Knowledge/Daily）
            json_daily_dir: JSON 日报目录（默认 ~/.keypulse/daily-summary）

        Returns:
            BaselineReport 对象
        """
        from datetime import datetime, timedelta

        if vault_daily_dir is None:
            vault_daily_dir = Path.home() / "Go" / "Knowledge" / "Daily"
        else:
            vault_daily_dir = Path(vault_daily_dir)

        if json_daily_dir is None:
            json_daily_dir = Path.home() / ".keypulse" / "daily-summary"
        else:
            json_daily_dir = Path(json_daily_dir)

        reports: list[EntityCoherenceReport] = []

        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        current = start

        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            report = self._judge_single_day(
                date_str,
                vault_daily_dir=vault_daily_dir,
                json_daily_dir=json_daily_dir,
            )
            if report:
                reports.append(report)
            current += timedelta(days=1)

        baseline = BaselineReport(
            start_date=start_date,
            end_date=end_date,
            reports=reports,
        )
        return baseline

    def _judge_single_day(
        self,
        date_str: str,
        *,
        vault_daily_dir: Path,
        json_daily_dir: Path,
    ) -> EntityCoherenceReport | None:
        """评测单天。若无对应文件则返回 None。"""
        # 读取 vault 渲染版日报
        vault_file = vault_daily_dir / f"{date_str}.md"
        if not vault_file.exists():
            return None

        try:
            daily_markdown = vault_file.read_text(encoding="utf-8")
        except Exception:
            return None

        # 优先从 markdown 提取完整的 topics（JSON 中 narrative 可能被截断）
        topics = self._extract_topics_from_markdown(daily_markdown)

        # 若 markdown 没有提取到 topics，再尝试从 JSON
        if not topics:
            json_file = json_daily_dir / f"{date_str}.json"
            if json_file.exists():
                try:
                    json_data = json.loads(json_file.read_text(encoding="utf-8"))
                    topics_raw = json_data.get("topics") or []
                    for topic in topics_raw:
                        if isinstance(topic, dict):
                            topic_display = topic.get("display") or topic.get("display_name") or ""
                            narrative = topic.get("narrative") or ""
                            if topic_display and narrative:
                                topics.append({
                                    "topic_display": str(topic_display),
                                    "narrative": str(narrative),
                                })
                except Exception:
                    pass

        if not topics:
            return None

        try:
            return self.judge_daily(date_str, daily_markdown, topics)
        except Exception:
            return None

    @staticmethod
    def _extract_topics_from_markdown(markdown: str) -> list[dict[str, str]]:
        """从 markdown 中提取 ### 主题及其内容。"""
        topics: list[dict[str, str]] = []
        lines = markdown.splitlines()

        current_topic = None
        current_content = []

        for line in lines:
            # 检测 ### 标题
            if line.startswith("### "):
                # 保存之前的 topic
                if current_topic is not None:
                    narrative = "\n".join(current_content).strip()
                    if narrative:
                        topics.append({
                            "topic_display": current_topic,
                            "narrative": narrative,
                        })
                current_topic = line[4:].strip()
                current_content = []
            elif current_topic is not None:
                # 若遇到其他标题级别或其他分隔，停止当前 topic
                if line.startswith("# ") or line.startswith("## "):
                    if current_content:
                        narrative = "\n".join(current_content).strip()
                        if narrative:
                            topics.append({
                                "topic_display": current_topic,
                                "narrative": narrative,
                            })
                    current_topic = None
                    current_content = []
                else:
                    current_content.append(line)

        # 保存最后一个 topic
        if current_topic is not None and current_content:
            narrative = "\n".join(current_content).strip()
            if narrative:
                topics.append({
                    "topic_display": current_topic,
                    "narrative": narrative,
                })

        return topics
