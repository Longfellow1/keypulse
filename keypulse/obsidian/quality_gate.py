"""Daily 报告质量护栏：写入前评分，劣化时拒写。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 退化模板（v2 fallback 的两个固定形态 + things fallback 的标记）
_TEMPLATE_PATTERNS = [
    re.compile(r"做了一些操作，?看不出方向"),
    re.compile(r"\d{1,2}:\d{2}-\d{1,2}:\d{2}\s+在\s+\S+\s+做\s+"),
    re.compile(r"narrative 生成失败，仅显示骨架"),
]


@dataclass(frozen=True)
class QualityScore:
    thing_count: int
    total_chars: int
    template_density: float
    unique_word_ratio: float


def score_daily(text: str) -> QualityScore:
    """对 daily markdown 做四指标评分。"""
    if not text:
        return QualityScore(0, 0, 0.0, 0.0)

    thing_count = len(re.findall(r"^### ", text, re.MULTILINE))
    total_chars = len(text)

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    paragraph_count = max(1, len(paragraphs))
    template_hits = sum(len(pat.findall(text)) for pat in _TEMPLATE_PATTERNS)
    template_density = template_hits / paragraph_count

    words = re.findall(r"[一-鿿]{2,}|[A-Za-z][A-Za-z0-9_-]+", text)
    unique_word_ratio = (len(set(words)) / len(words)) if words else 0.0

    return QualityScore(thing_count, total_chars, template_density, unique_word_ratio)


def evaluate(new_score: QualityScore, baseline: QualityScore | None) -> tuple[bool, str]:
    """新分数是否达标。返回 (是否写入, 原因短语)。"""
    if new_score.thing_count < 3:
        return False, f"thing_count={new_score.thing_count}<3"
    if new_score.template_density > 0.15:
        return False, f"template_density={new_score.template_density:.2f}>0.15"
    if new_score.unique_word_ratio < 0.4:
        return False, f"unique_word_ratio={new_score.unique_word_ratio:.2f}<0.4"
    if baseline is not None and baseline.total_chars > 0:
        if new_score.total_chars < baseline.total_chars * 0.6:
            return False, f"total_chars={new_score.total_chars} < baseline {baseline.total_chars}*0.6"
    return True, "ok"


def should_write_daily(new_text: str, existing_path: Path) -> tuple[bool, str, QualityScore, QualityScore | None]:
    """判断是否应当覆盖 existing_path。返回 (写, 原因, 新分, 旧分或 None)。

    新文件不存在或读不到 → 只看新版自身门槛。
    """
    new_score = score_daily(new_text)
    baseline: QualityScore | None = None
    if existing_path.exists():
        try:
            old_text = existing_path.read_text(encoding="utf-8")
            baseline = score_daily(old_text)
        except OSError:
            baseline = None

    ok, reason = evaluate(new_score, baseline)
    if not ok:
        logger.warning(
            "quality_gate REFUSED %s: %s | new=%s | old=%s",
            existing_path.name,
            reason,
            new_score,
            baseline,
        )
    else:
        logger.info(
            "quality_gate PASSED %s: %s | new=%s | old=%s",
            existing_path.name,
            reason,
            new_score,
            baseline,
        )
    return ok, reason, new_score, baseline
