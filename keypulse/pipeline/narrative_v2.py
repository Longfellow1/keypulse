from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from keypulse.pipeline.evidence import EvidenceUnit, enrich_with_evidence, load_profile_dict, sanitize_unit
from keypulse.pipeline.gates import QualityReport, should_generate_narrative, summarize_quality
from keypulse.pipeline.signals import collect_browser_signals, collect_filesystem_signals, enrich_unit_with_signals
from keypulse.pipeline.triggers import record_trigger, record_trigger_pending

logger = logging.getLogger(__name__)

_OFFLINE_PLACEHOLDER = "这段时间机器不在，无法获取工作内容。"

_PASS1_PROMPT_TEMPLATE = """\
/no_think
根据证据写一句中文，说明"用户这段时间在做什么事"。

【输入】
时间：{ts_start_hm}-{ts_end_hm}
应用：{where}
内容：{what}

【要求】
1. 主语是"你"。写意图（他想做什么），不是界面动作（点了什么键）。
   × 你点击了登录按钮
   ○ 你在试着登进后台

2. 应用名 / 文件名 / 标签里的长复合词，只取第一个英文词（按 - _ 空格切分）：
   "Google Chrome - lang (Har)" → Chrome
   "carmind-cce-test-a30" → carmind
   "corpusflow-google-chrome-lang-har" → corpusflow
   "feishu_agent.log" → feishu 日志
   "stats.sh" → stats 脚本

3. 不许出现：
   - 完整命令（chmod +x *.sh / kubectl get pods / ./stats.sh）→ 改写成"一个脚本"/"一个命令"
   - 完整路径（/mnt/paas/bg/...）→ 改写成"某个目录"
   - 报错原文（bash: xxx: No such file）→ 改写成"文件没找到"/"权限被拒"

4. 看不出意图就说："你在 [应用缩写] 里做了一些操作，看不出方向。"

5. 一句话，≤40 字，不加引号、不加前缀。\
"""

_PASS2A_PROMPT_TEMPLATE = """\
/no_think
任务：把下面 {n} 句事实按时间顺序拼接成一段话。只做顺序整理，不做创作。

【输入：{n} 句事实（已按时间顺序）】
{evidence_lines}

【硬规则】
1. 每句原文都要被保留含义，允许合并相邻语义相同的句子。
2. 严禁引入任何证据里没有的内容：
   - 不加数值、次数（× "试了三次"）
   - 不加动作、场景（× "喝咖啡" × "看窗外" × "关电脑"）
   - 不加心理、情绪（× "心里一紧" × "像自言自语"）
   - 不加新名词（× 从 "lioncarmind-..." 衍生出 "lioncarmind-03"）
3. 只允许加的词：
   - 时间过渡：上午、中午、下午、傍晚
   - 连接词：先是、接着、后来、于是、但、不过
4. 机器不在的时段：用一句话带过（例 "中午这段没在电脑前"），不展开。
5. 一段文字，不分段、不加标题、不加编号。
6. 字数跟原句总字数差不多，不刻意拉长。

【输出】
直接输出拼接后的正文，不加前缀。\
"""

_PASS2B_PROMPT_TEMPLATE = """\
/no_think
任务：对下面这段文字做轻度润色——只调句子衔接，不改事实。

【输入】
{pass2a_text}

【硬规则】
1. 原文所有事实必须保留：动词、名词、数值、应用名、文件名、时间段。
2. 只能修：啰嗦的重复连接词、"然后然后"这种、不通顺的过渡。
3. 严禁加任何新内容：
   × 新动作（喝咖啡、看窗外、站在窗前、关电脑）
   × 新数值、次数、时长
   × 心理、情绪、感受
   × 场景描写（夕阳、树影、阳光、窗外）
   × 结尾升华（"像呼吸一样"、"像心跳一样"）
4. 分 2-4 自然段，字数跟输入 ±10%。
5. 不加标题，不加"好的，以下是..."前缀。

【输出】
直接输出润色后的正文。\
"""


class GatewayProtocol(Protocol):
    def render(self, prompt: str) -> str:
        ...


@dataclass
class TwoPassResult:
    pass1_sentences: list[str]
    pass2_narrative: str
    quality_report: QualityReport
    units_used: list[EvidenceUnit]
    pending_ids: list[int]


def _fmt_hm(dt: datetime) -> str:
    local = dt.astimezone() if dt.tzinfo else dt
    return local.strftime("%H:%M")


def _factual_fallback(unit: EvidenceUnit) -> str:
    ts_start = _fmt_hm(unit.ts_start)
    ts_end = _fmt_hm(unit.ts_end)
    where = unit.where or "未知应用"
    what = (unit.what or "").strip()[:30]
    return f"{ts_start}-{ts_end} 在 {where} 做 {what}" if what else f"{ts_start}-{ts_end} 在 {where}"


def _build_pass1_prompt(unit: EvidenceUnit) -> str:
    return _PASS1_PROMPT_TEMPLATE.format(
        ts_start_hm=_fmt_hm(unit.ts_start),
        ts_end_hm=_fmt_hm(unit.ts_end),
        where=unit.where or "未知应用",
        what=(unit.what or "").strip() or "未知内容",
    )


def _build_pass2a_prompt(sentences: list[str]) -> str:
    evidence_lines = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    return _PASS2A_PROMPT_TEMPLATE.format(
        n=len(sentences),
        evidence_lines=evidence_lines,
    )


def _build_pass2b_prompt(pass2a_text: str) -> str:
    return _PASS2B_PROMPT_TEMPLATE.format(pass2a_text=pass2a_text)


def run_two_pass(
    units: list[EvidenceUnit],
    *,
    db_path: Path,
    gateway: Any,
    profile_dict: dict[str, Any],
    date_str: str,
    now: datetime,
) -> TwoPassResult:
    quality_report = summarize_quality(units)
    pending_ids: list[int] = []
    pass1_sentences: list[str] = []

    for unit in units:
        if not unit.machine_online:
            pass1_sentences.append(_OFFLINE_PLACEHOLDER)
            continue

        pending_id = record_trigger_pending(
            "T4_pass1",
            now=now,
            db_path=db_path,
            note=f"date={date_str} where={unit.where}",
        )
        pending_ids.append(pending_id)

        try:
            prompt = _build_pass1_prompt(unit)
            sentence = gateway.render(prompt)
            sentence = sentence.strip()
            if not sentence:
                raise ValueError("empty response from gateway")
            record_trigger(
                "T4_pass1",
                now=now,
                db_path=db_path,
                outcome="ran:ok",
                pending_id=pending_id,
            )
        except Exception as exc:
            logger.warning(
                "pass1 gateway failure unit.where=%s exc_type=%s exc=%s; using factual fallback",
                unit.where,
                type(exc).__name__,
                exc,
            )
            sentence = _factual_fallback(unit)
            record_trigger(
                "T4_pass1",
                now=now,
                db_path=db_path,
                outcome="ran:fail",
                note=str(exc)[:120],
                pending_id=pending_id,
            )

        pass1_sentences.append(sentence)

    ok, reason = should_generate_narrative(quality_report)
    if not ok:
        logger.info("skip pass2 gate reason=%s date=%s", reason, date_str)
        return TwoPassResult(
            pass1_sentences=pass1_sentences,
            pass2_narrative="今日证据不足，跳过叙事",
            quality_report=quality_report,
            units_used=units,
            pending_ids=pending_ids,
        )

    pending_id_pass2a = record_trigger_pending(
        "T4_pass2a",
        now=now,
        db_path=db_path,
        note=f"date={date_str} units={len(units)}",
    )
    pending_ids.append(pending_id_pass2a)

    try:
        prompt2a = _build_pass2a_prompt(pass1_sentences)
        pass2a_text = gateway.render(prompt2a).strip()
        if not pass2a_text:
            raise ValueError("empty pass2a response from gateway")
        record_trigger(
            "T4_pass2a",
            now=now,
            db_path=db_path,
            outcome="ran:ok",
            pending_id=pending_id_pass2a,
        )
    except Exception as exc:
        logger.warning(
            "pass2a gateway failure exc_type=%s exc=%s; using pass1 join",
            type(exc).__name__,
            exc,
        )
        pass2a_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(pass1_sentences))
        record_trigger(
            "T4_pass2a",
            now=now,
            db_path=db_path,
            outcome="ran:fail",
            note=str(exc)[:120],
            pending_id=pending_id_pass2a,
        )

    pending_id_pass2b = record_trigger_pending(
        "T4_pass2b",
        now=now,
        db_path=db_path,
        note=f"date={date_str} pass2a_len={len(pass2a_text)}",
    )
    pending_ids.append(pending_id_pass2b)

    try:
        prompt2b = _build_pass2b_prompt(pass2a_text)
        pass2_narrative = gateway.render(prompt2b).strip()
        if not pass2_narrative:
            raise ValueError("empty pass2b response from gateway")
        record_trigger(
            "T4_pass2b",
            now=now,
            db_path=db_path,
            outcome="ran:ok",
            pending_id=pending_id_pass2b,
        )
    except Exception as exc:
        logger.warning(
            "pass2b gateway failure exc_type=%s exc=%s; falling back to pass2a output",
            type(exc).__name__,
            exc,
        )
        pass2_narrative = pass2a_text
        record_trigger(
            "T4_pass2b",
            now=now,
            db_path=db_path,
            outcome="ran:fail",
            note=str(exc)[:120],
            pending_id=pending_id_pass2b,
        )

    return TwoPassResult(
        pass1_sentences=pass1_sentences,
        pass2_narrative=pass2_narrative,
        quality_report=quality_report,
        units_used=units,
        pending_ids=pending_ids,
    )


def render_v2_narrative(
    work_blocks: list,
    *,
    model_gateway: Any,
    db_path: Path | str,
    date_str: str,
    now: datetime | None = None,
    watch_paths: list[Path] | None = None,
) -> str:
    """High-level facade: work_blocks -> v2 narrative string.
    Shared by pipeline/write.py and obsidian/exporter.py.
    Returns narrative body (no decisions section).
    """
    logger.info("v2_narrative_enter blocks=%d date=%s", len(work_blocks or []), date_str)
    if not work_blocks:
        return "今日无证据"

    resolved_db = Path(db_path)
    now = now or datetime.now(tz=timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    enriched_pairs = enrich_with_evidence(work_blocks, resolved_db)
    raw_units = [unit for _, units in enriched_pairs for unit in units]
    all_units = [u for u in (sanitize_unit(u) for u in raw_units) if u is not None]
    dropped = len(raw_units) - len(all_units)
    if dropped:
        logger.info("v2 sanitize dropped %d sensitive units (login/password/code)", dropped)
    profile_dict = load_profile_dict(resolved_db)

    fs_signals = collect_filesystem_signals(
        day_start, now, watch_paths=watch_paths or []
    )
    browser_signals = collect_browser_signals(day_start, now, db_path=resolved_db)
    enriched_units = [
        enrich_unit_with_signals(u, fs_signals=fs_signals, browser_signals=browser_signals)
        for u in all_units
    ]

    quality_report = summarize_quality(enriched_units)
    gate_ok, gate_reason = should_generate_narrative(quality_report)
    if not gate_ok:
        logger.info("v2 gate skip reason=%s date=%s", gate_reason, date_str)
        return "今日证据不足，跳过叙事"

    try:
        result = run_two_pass(
            enriched_units,
            db_path=resolved_db,
            gateway=model_gateway,
            profile_dict=profile_dict,
            date_str=date_str or now.strftime("%Y-%m-%d"),
            now=now,
        )
        return result.pass2_narrative
    except Exception as exc:
        logger.error("v2 narrative fallback exc_type=%s exc=%s", type(exc).__name__, exc)
        return ""
