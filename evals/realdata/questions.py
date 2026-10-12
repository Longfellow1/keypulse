"""Question file (JSONL) parsing. One JSON object per line."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

EXPECTS = ("answer", "not_found", "must_not_see")
VERDICTS = ("correct", "partial", "wrong")


class QuestionError(ValueError):
    pass


def _str_list(value, field_name: str, qid: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, int)):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in value):
        raise QuestionError(f"{qid}: {field_name} must be a string or a list of strings")
    return [str(x) for x in value]


@dataclass(frozen=True)
class Question:
    id: str
    group: str
    grant: tuple[str, ...]
    query: str
    gold_answer: str
    expect: str = "answer"
    gold_event_ids: tuple[str, ...] = ()
    gold_match: tuple[str, ...] = ()
    human_verdicts: dict = field(default_factory=dict)  # optional {"kp": "correct", ...} for judge agreement

    @property
    def event_ids(self) -> set[str]:
        """gold_event_ids normalised to the ``evt_N`` form used in tool output."""
        out = set()
        for e in self.gold_event_ids:
            e = e.strip()
            out.add(e if e.startswith("evt_") else f"evt_{e}")
        return out


def parse_question(obj: dict, line_no: int = 0) -> Question:
    qid = str(obj.get("id") or f"line{line_no}")
    for key in ("id", "group", "grant", "query", "gold_answer"):
        if obj.get(key) in (None, "", []):
            raise QuestionError(f"{qid}: missing required field {key!r}")
    grant = obj["grant"]
    if not isinstance(grant, list) or not all(isinstance(b, str) and b for b in grant):
        raise QuestionError(f"{qid}: grant must be a non-empty list of bucket names")
    expect = obj.get("expect", "answer")
    if expect not in EXPECTS:
        raise QuestionError(f"{qid}: expect must be one of {EXPECTS}")
    human = obj.get("human_verdicts") or {}
    if not isinstance(human, dict) or any(v not in VERDICTS for v in human.values()):
        raise QuestionError(f"{qid}: human_verdicts must map arm -> {VERDICTS}")
    q = Question(
        id=str(obj["id"]), group=str(obj["group"]), grant=tuple(sorted(set(grant))), query=str(obj["query"]),
        gold_answer=str(obj["gold_answer"]), expect=expect,
        gold_event_ids=tuple(_str_list(obj.get("gold_event_ids"), "gold_event_ids", qid)),
        gold_match=tuple(_str_list(obj.get("gold_match"), "gold_match", qid)), human_verdicts=human,
    )
    if expect == "must_not_see" and not (q.gold_event_ids or q.gold_match):
        raise QuestionError(f"{qid}: must_not_see needs gold_event_ids or gold_match")
    return q


def load_questions(path: Path) -> list[Question]:
    out, seen = [], set()
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise QuestionError(f"line {n}: invalid JSON ({exc.msg})") from None
        if not isinstance(obj, dict):
            raise QuestionError(f"line {n}: expected a JSON object")
        q = parse_question(obj, n)
        if q.id in seen:
            raise QuestionError(f"duplicate question id {q.id!r}")
        seen.add(q.id)
        out.append(q)
    return out
