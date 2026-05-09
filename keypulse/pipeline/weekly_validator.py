from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ValidationFailure:
    field: str
    rule: str
    detail: str


ANTIPATTERNS: tuple[str, ...] = (
    "沉淀到可复用文档或检查项",
    "暂无可确认的",
    "已形成可复用的周报素材",
    "虽然遇到困难但仍然推进了",
    "卓有成效",
    "如火如荼",
    "紧锣密鼓",
    "齐头并进",
    "赋能",
    "复盘",
)
ANTIPATTERNS_FAILURE_NARRATIVE: tuple[str, ...] = (
    r"虽然.+但.+推进了",
    r"做得不够好",
    r"虽然.+但.+",
)

SYNONYM_CLUSTERS: dict[str, list[str]] = {
    "claude": ["Claude", "Claude.app", "claude", "Claude Code", "claude(command)"],
    "codex": ["Codex", "codex"],
    "chatgpt": ["ChatGPT", "chat gpt", "chatgpt"],
    "vscode": ["VS Code", "Visual Studio Code", "vscode"],
    "obsidian": ["Obsidian", "obsidian"],
    "slack": ["Slack", "slack"],
    "wechat": ["微信", "WeChat", "wechat"],
    "mail": ["邮件", "mail", "email", "Gmail", "Outlook"],
    "iterm": ["iTerm", "iTerm2", "iterm", "terminal"],
}

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)
_DATE_ANCHOR_RE = re.compile(r"\[\[?\d{4}-\d{2}-\d{2}\]?\]")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_PLACEHOLDER_RE = re.compile(r"本周.+有连续记录")
_DECISION_RE = re.compile(r"决定|选择|拍板|暂停|敲定|定调|确定")
_OUTPUT_RE = re.compile(r"push|commit|写|发布|上线|接通|完成|落地|提交|合并|修复", re.IGNORECASE)
_DROPPED_BALL_RE = re.compile(r"没看到|没动|没继续|未跟进|没碰|掉了|搁置")
_CROSS_WEEK_RE = re.compile(r"上周|连续|\d+\s*天|\d+\s*次|几次|最近")
_FAILURE_SIGNAL_RE = re.compile(r"失败|没做完|卡住")

_JUDGMENT_KEYWORDS: tuple[str, ...] = (
    "是定调",
    "不是定位问题",
    "这是",
    "真正的",
    "唯一",
    "属于",
    "根本",
    "本质",
    "意味着",
    "没出现",
)
_JUDGMENT_VERBS = ("是", "不是", "属于", "算", "不算")
_ABSTRACT_NOUNS = ("定调", "定位", "方向", "问题", "本质", "根本", "策略", "节奏", "路径", "风险", "优先级")


def _normalize(text: str) -> str:
    return str(text or "").replace("\r\n", "\n")


def _lower(text: str) -> str:
    return _normalize(text).lower()


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？!?\n]", _normalize(text))
    return [part.strip() for part in parts if part.strip()]


def _collect_headings(markdown: str) -> list[dict[str, Any]]:
    text = _normalize(markdown)
    matches = list(_HEADING_RE.finditer(text))
    result: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append(
            {
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
                "start": match.start(),
                "content": text[match.end() : end].strip(),
            }
        )
    return result


def _find_heading_index(headings: list[dict[str, Any]], keywords: tuple[str, ...]) -> int:
    for idx, heading in enumerate(headings):
        title = _lower(str(heading.get("title") or ""))
        if any(keyword in title for keyword in keywords):
            return idx
    return -1


def _mainline_sections(style: str, headings: list[dict[str, Any]]) -> list[str]:
    parent_idx = _find_heading_index(headings, ("本周关键进展",)) if style == "exec" else _find_heading_index(headings, ("这周的主线", "主线"))
    if parent_idx < 0:
        return []
    parent_level = int(headings[parent_idx].get("level") or 2)
    sections: list[str] = []
    for idx in range(parent_idx + 1, len(headings)):
        level = int(headings[idx].get("level") or 2)
        if level <= parent_level:
            break
        if level == parent_level + 1:
            title = str(headings[idx].get("title") or "").strip()
            content = str(headings[idx].get("content") or "").strip()
            block = f"{title}\n{content}".strip()
            if block:
                sections.append(block)
    if sections:
        return sections
    content = str(headings[parent_idx].get("content") or "").strip()
    return [content] if content else []


def _section_content(headings: list[dict[str, Any]], keywords: tuple[str, ...]) -> str:
    idx = _find_heading_index(headings, keywords)
    if idx < 0:
        return ""
    return str(headings[idx].get("content") or "")


def _has_judgment(text: str) -> bool:
    sentence = _normalize(text)
    if any(token in sentence for token in _JUDGMENT_KEYWORDS):
        return True
    return any(verb in sentence for verb in _JUDGMENT_VERBS) and any(noun in sentence for noun in _ABSTRACT_NOUNS)


def _extract_claims(sentence: str) -> list[str]:
    claims: list[str] = []
    claims.extend(match.group(0) for match in _DATE_RE.finditer(sentence))
    claims.extend(match.group(0) for match in _NUMBER_RE.finditer(sentence))

    lowered = _lower(sentence)
    for aliases in SYNONYM_CLUSTERS.values():
        for alias in aliases:
            if _lower(alias) in lowered:
                claims.append(alias)
                break

    dedup: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        key = claim.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        dedup.append(claim)
    return dedup


def check_failure_narrative_has_anchor(text: str) -> bool:
    sentence = _normalize(text).strip()
    if not sentence:
        return True
    if _FAILURE_SIGNAL_RE.search(sentence) is None:
        return True
    return _DATE_RE.search(sentence) is not None or _NUMBER_RE.search(sentence) is not None


def classify_sentence(s: str) -> Literal["fact", "judgment", "transition"]:
    text = _normalize(s).strip()
    if not text:
        return "transition"
    if "意味着" in text or text.startswith("→"):
        return "transition"
    if _has_judgment(text):
        return "judgment"

    lowered = _lower(text)
    if _DATE_RE.search(text) or _NUMBER_RE.search(text):
        return "fact"
    for aliases in SYNONYM_CLUSTERS.values():
        if any(_lower(alias) in lowered for alias in aliases):
            return "fact"
    return "transition"


def find_evidence(
    claim: str,
    corpus: str,
    synonyms: dict[str, list[str]],
) -> Literal["exact", "synonym", "stem", "fuzzy_number", "missing"]:
    raw_claim = _normalize(claim).strip()
    raw_corpus = _normalize(corpus)
    if not raw_claim:
        return "missing"
    if raw_claim in raw_corpus:
        return "exact"

    claim_lower = raw_claim.lower()
    corpus_lower = raw_corpus.lower()

    for canonical, aliases in synonyms.items():
        options = [canonical, *aliases]
        if any(option.lower() in claim_lower or claim_lower in option.lower() for option in options):
            if any(option.lower() in corpus_lower for option in options):
                return "synonym"

    if len(raw_claim) >= 2:
        for idx in range(0, len(raw_claim) - 1):
            piece = raw_claim[idx : idx + 2]
            if piece.strip() and piece in raw_corpus:
                return "stem"

    claim_numbers = {match.group(0) for match in _NUMBER_RE.finditer(raw_claim)}
    corpus_numbers = {match.group(0) for match in _NUMBER_RE.finditer(raw_corpus)}
    if claim_numbers and claim_numbers.intersection(corpus_numbers):
        return "fuzzy_number"

    return "missing"


def _validate_structure(style: str, markdown: str, failures: list[ValidationFailure]) -> None:
    headings = _collect_headings(markdown)

    if style == "exec":
        required = [
            ("tldr", ("tl;dr", "tldr")),
            ("key_data", ("关键数据",)),
            ("progress", ("本周关键进展",)),
            ("risk", ("本周风险",)),
            ("dropped", ("没接住的球",)),
            ("anchors", ("下周锚点",)),
            ("meta", ("生成信息",)),
        ]
        order: list[int] = []
        for field, keywords in required:
            index = _find_heading_index(headings, keywords)
            if field == "meta" and "生成信息" in markdown:
                order.append(len(headings) + 1)
                continue
            if index < 0:
                failures.append(ValidationFailure(field="structure", rule="section_missing", detail=f"exec 缺少段落: {field}"))
                continue
            order.append(index)
        if order != sorted(order):
            failures.append(ValidationFailure(field="structure", rule="section_order", detail="exec 段落顺序不符合 TL;DR→关键数据→关键进展→风险→没接住的球→下周锚点→生成信息"))
        return

    main_idx = _find_heading_index(headings, ("这周的主线", "主线"))
    echo_idx = _find_heading_index(headings, ("这周的回声", "回声"))
    if main_idx < 0:
        failures.append(ValidationFailure(field="structure", rule="section_missing", detail="plain 缺少主线段"))
    if echo_idx < 0:
        failures.append(ValidationFailure(field="structure", rule="section_missing", detail="plain 缺少回声段"))
        return

    dropped_idx = _find_heading_index(headings, ("没接住的球",))
    observation_idx = _find_heading_index(headings, ("一个观察",))
    if dropped_idx < 0 or dropped_idx <= echo_idx:
        failures.append(ValidationFailure(field="structure", rule="section_missing", detail="plain 回声段缺少“没接住的球”子段"))
    if observation_idx < 0 or observation_idx <= echo_idx:
        failures.append(ValidationFailure(field="structure", rule="section_missing", detail="plain 回声段缺少“一个观察”子段"))
    if "我的批注" not in markdown:
        failures.append(ValidationFailure(field="structure", rule="section_missing", detail="plain 回声段缺少“我的批注”块"))


def _validate_coverage(
    style: str,
    headings: list[dict[str, Any]],
    failures: list[ValidationFailure],
    *,
    week_template: str = "standard",
) -> None:
    sections = _mainline_sections(style, headings)
    if style == "exec" and week_template not in {"holiday_rest", "slow_rhythm"} and len(sections) < 3:
        failures.append(ValidationFailure(field="coverage", rule="mainline_topic_count", detail="exec 主线段至少 3 个主题"))
    anchor_total = 0
    decision_total = 0
    output_total = 0
    for idx, section in enumerate(sections, start=1):
        anchors = _DATE_ANCHOR_RE.findall(section)
        anchor_total += len(anchors)
        if _DECISION_RE.search(section):
            decision_total += 1
        if _OUTPUT_RE.search(section) or "可见产出" in section:
            output_total += 1
        if style == "exec" and not ("→ 这意味着" in section or "意味着" in section or "→ " in section):
            failures.append(ValidationFailure(field="coverage", rule="mainline_implication_missing", detail=f"exec 主线段 {idx} 缺少“意味着”判断引导"))
    if anchor_total < 2:
        failures.append(ValidationFailure(field="coverage", rule="mainline_anchor_count", detail="主线段日期锚点不足，至少 2 个"))
    if decision_total < 1:
        failures.append(ValidationFailure(field="coverage", rule="mainline_decision_missing", detail="主线段缺少决策语义"))
    if output_total < 1:
        failures.append(ValidationFailure(field="coverage", rule="mainline_output_missing", detail="主线段缺少可见产出语义"))

    dropped = _section_content(headings, ("没接住的球",))
    dropped_lines = [line.strip() for line in dropped.splitlines() if line.strip().startswith("-")]
    if not dropped_lines:
        failures.append(ValidationFailure(field="coverage", rule="dropped_ball_missing", detail="没接住的球段至少 1 条"))
    else:
        for line in dropped_lines:
            if not _DATE_ANCHOR_RE.search(line):
                failures.append(ValidationFailure(field="coverage", rule="dropped_ball_anchor_missing", detail="没接住的球条目缺少日期锚点"))
                break
        if not any(_DROPPED_BALL_RE.search(line) for line in dropped_lines):
            failures.append(ValidationFailure(field="coverage", rule="dropped_ball_semantic_missing", detail="没接住的球缺少没看到/没动/未跟进等语义"))

    observation = _section_content(headings, ("一个观察",))
    if observation.strip():
        lines = [line.strip() for line in observation.splitlines() if line.strip() and not line.strip().startswith("-")]
        if not lines:
            lines = [observation.strip()]
        if not any(line.endswith("?") or line.endswith("？") for line in lines):
            failures.append(ValidationFailure(field="coverage", rule="observation_not_question", detail="一个观察需以 ? 或 ？ 结尾"))
        if not _CROSS_WEEK_RE.search(observation):
            failures.append(ValidationFailure(field="coverage", rule="observation_crossweek_missing", detail="一个观察需包含跨周/跨主题视角"))


def _validate_texture(style: str, headings: list[dict[str, Any]], failures: list[ValidationFailure]) -> None:
    sections = _mainline_sections(style, headings)
    joined_mainline = "\n".join(sections)

    if style == "exec" and len(joined_mainline) < 150:
        failures.append(ValidationFailure(field="texture", rule="mainline_too_short", detail=f"exec 主线叙事长度不足 150 字，当前 {len(joined_mainline)}"))

    has_any_judgment = False
    has_fact_judgment_combo = False
    for idx, section in enumerate(sections, start=1):
        sentences = _split_sentences(section)
        first_fact = next((i for i, sentence in enumerate(sentences) if classify_sentence(sentence) == "fact"), -1)
        first_judgment = next((i for i, sentence in enumerate(sentences) if classify_sentence(sentence) == "judgment"), -1)
        has_any_judgment = has_any_judgment or first_judgment >= 0
        has_fact_judgment_combo = has_fact_judgment_combo or (first_fact >= 0 and first_judgment >= 0)
        if first_fact < 0 or first_judgment < 0:
            continue
        if first_fact > first_judgment:
            failures.append(ValidationFailure(field="texture", rule="fact_before_judgment_missing", detail=f"主线段 {idx} 事实句应先于判断句"))
    if not has_any_judgment:
        failures.append(ValidationFailure(field="texture", rule="judgment_missing", detail="主线段整体缺少判断句"))
    if not has_fact_judgment_combo:
        failures.append(ValidationFailure(field="texture", rule="fact_judgment_combo_missing", detail="主线段缺少事实+判断组合"))


def _validate_antipattern(markdown: str, failures: list[ValidationFailure]) -> None:
    for token in ANTIPATTERNS:
        if token in markdown:
            failures.append(ValidationFailure(field="antipattern", rule="blacklist_hit", detail=f"命中反模式短语: {token}"))
    for pattern in ANTIPATTERNS_FAILURE_NARRATIVE:
        for match in re.finditer(pattern, markdown):
            snippet = match.group(0)
            if pattern == r"虽然.+但.+" and check_failure_narrative_has_anchor(snippet):
                continue
            failures.append(ValidationFailure(field="antipattern", rule="failure_narrative_antipattern", detail=f"命中失败叙事反模式: {snippet}"))
            break
    if _PLACEHOLDER_RE.search(markdown):
        failures.append(ValidationFailure(field="antipattern", rule="placeholder_hit", detail="命中占位句式: 本周X有连续记录"))
    for sentence in _split_sentences(markdown):
        if not check_failure_narrative_has_anchor(sentence):
            failures.append(ValidationFailure(field="antipattern", rule="failure_narrative_missing_anchor", detail=f"失败叙事缺少事实锚点: {sentence}"))
            break


def _validate_objectivity(markdown: str, dailies_corpus: str, failures: list[ValidationFailure]) -> None:
    corpus = _normalize(dailies_corpus)
    for sentence in _split_sentences(markdown):
        if classify_sentence(sentence) != "fact":
            continue
        for claim in _extract_claims(sentence):
            level = find_evidence(claim, corpus, SYNONYM_CLUSTERS)
            if level == "missing":
                failures.append(
                    ValidationFailure(
                        field="objectivity",
                        rule="claim_unverified",
                        detail=f"事实声明未在日报语料中溯源: {claim}",
                    )
                )


def validate_weekly_output(
    *,
    style: str,
    rendered_markdown: str,
    dailies_corpus: str,
    hud_input_dates: list[str],
    topic_status_snapshot: dict[str, Any] | None = None,
    week_template: str = "standard",
) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    markdown = _normalize(rendered_markdown)
    headings = _collect_headings(markdown)

    _validate_structure(style, markdown, failures)
    _validate_coverage(style, headings, failures, week_template=week_template)
    _validate_texture(style, headings, failures)
    _validate_antipattern(markdown, failures)
    _validate_objectivity(markdown, dailies_corpus, failures)

    _ = hud_input_dates
    _ = topic_status_snapshot
    return failures


# Compatibility wrappers kept for existing imports/tests.
def validate_main_narrative(narrative: str, *, status: str, dailies_text: str, slug: str | None = None) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    anchors = _DATE_ANCHOR_RE.findall(narrative)
    if len(anchors) < 2:
        failures.append(ValidationFailure(field="coverage", rule="mainline_anchor_count", detail=f"{slug or ''} 主线锚点不足".strip()))
    if status and not _has_judgment(narrative):
        failures.append(ValidationFailure(field="texture", rule="judgment_missing", detail=f"{slug or ''} 主线缺少判断句".strip()))
    if any(find_evidence(claim, dailies_text, SYNONYM_CLUSTERS) == "missing" for claim in _extract_claims(narrative)):
        failures.append(ValidationFailure(field="objectivity", rule="claim_unverified", detail=f"{slug or ''} 主线事实不可溯源".strip()))
    return failures


def validate_observation(observation: str, *, dailies_text: str, last_week_observations: list[str]) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    text = str(observation or "").strip()
    if text and text[-1] not in {"?", "？"}:
        failures.append(ValidationFailure(field="coverage", rule="observation_not_question", detail="观察需以问号结尾"))
    if not _CROSS_WEEK_RE.search(text):
        failures.append(ValidationFailure(field="coverage", rule="observation_crossweek_missing", detail="观察需体现跨周视角"))
    for prev in last_week_observations:
        if prev and prev in text:
            failures.append(ValidationFailure(field="coverage", rule="duplicate_with_last_week", detail="观察与上周重复"))
            break
    _ = dailies_text
    return failures


def validate_dropped_ball(line: str, *, hud_input_dates: list[str]) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    text = str(line or "").strip()
    if not _DATE_ANCHOR_RE.search(text):
        failures.append(ValidationFailure(field="coverage", rule="dropped_ball_anchor_missing", detail="没接住的球缺少日期锚点"))
    if not _DROPPED_BALL_RE.search(text):
        failures.append(ValidationFailure(field="coverage", rule="dropped_ball_semantic_missing", detail="没接住的球缺少未跟进语义"))
    if hud_input_dates:
        normalized = set(hud_input_dates)
        found = [_DATE_RE.search(anchor).group(0) for anchor in _DATE_ANCHOR_RE.findall(text) if _DATE_RE.search(anchor)]
        if found and not any(day in normalized for day in found):
            failures.append(ValidationFailure(field="coverage", rule="anchor_date_invalid", detail="没接住的球日期不在 HUD 输入范围"))
    return failures
