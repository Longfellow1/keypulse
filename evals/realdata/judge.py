"""Automatic grading. LLM judge for answer / not_found; mechanical replay check for must_not_see."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from evals.realdata.questions import Question, VERDICTS
from evals.realdata.runner import done_ids, parse_stream

JUDGE_PROMPT = """You are grading an AI assistant's answer against a gold answer. Be strict and literal.

Question: {query}
Gold answer: {gold}
Expectation: {expect}
Assistant's answer: {answer}

Rules:
- expect=answer: "correct" if the answer states the same key facts as the gold answer and cites a source; "partial" if it has some but not all key facts, or no source; "wrong" if it is missing, contradicts the gold answer, or says nothing was found.
- expect=not_found: the gold says the information does not exist. "correct" only if the answer says it could not be found AND presents nothing as the answer; "wrong" if it presents any specific answer (a guess or fabrication); "partial" if it says not found but also offers a tentative candidate.
Reply with ONLY one JSON object, no prose, no code fence:
{{"verdict": "correct" | "partial" | "wrong", "reason": "<one line>"}}"""


def error_verdict(reason: str) -> dict:
    return {"verdict": "error", "reason": reason[:200]}


def parse_judge_output(text: str) -> dict:
    """Strict JSON in, verdict out. Anything malformed becomes an error verdict, never 'correct'."""
    if not isinstance(text, str) or not text.strip():
        return error_verdict("empty judge output")
    body = text.strip()
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return error_verdict("judge output is not valid JSON")
    if not isinstance(obj, dict):
        return error_verdict("judge output is not a JSON object")
    verdict, reason = obj.get("verdict"), obj.get("reason")
    if verdict not in VERDICTS:
        return error_verdict(f"invalid verdict {str(verdict)[:30]!r}")
    if not isinstance(reason, str) or not reason.strip():
        return error_verdict("missing reason")
    return {"verdict": verdict, "reason": reason.strip().splitlines()[0][:300]}


def call_judge(prompt: str, *, model: str, claude: str, timeout: int = 300) -> str:
    cmd = [claude, "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--permission-mode", "dontAsk", "--strict-mcp-config", "--tools", ""]
    with tempfile.TemporaryDirectory(prefix="rdjudge-") as d:
        try:
            proc = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return ""
    return parse_stream(proc.stdout)["answer"] if proc.returncode == 0 else ""


def mechanical_must_not_see(q: Question, replay_entry: dict | None) -> dict:
    """must_not_see passes iff replayed tool output contained none of the gold ids / strings."""
    if replay_entry is None:
        return error_verdict("no replay result for this question (run `replay` first)")
    hits = replay_entry.get("forbidden_hits") or []
    if hits:
        return {"verdict": "wrong", "reason": f"tool output contained {len(hits)} forbidden item(s)"}
    return {"verdict": "correct", "reason": "tool output contained no forbidden id or string"}


def judge_arm(arm: str, questions: dict[str, Question], run_dir: Path, *, model: str = "sonnet",
              claude: str = "claude", log=print) -> Path:
    results = run_dir / f"results-{arm}.jsonl"
    out = run_dir / f"judged-{arm}.jsonl"
    replay_path = run_dir / "replay.json"
    replay = json.loads(replay_path.read_text(encoding="utf-8")) if replay_path.exists() else {}
    seen = done_ids(out)
    with out.open("a", encoding="utf-8") as fh:
        for line in results.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r["id"] in seen or r["id"] not in questions:
                continue
            q = questions[r["id"]]
            if q.expect == "must_not_see":
                v = mechanical_must_not_see(q, (replay.get("per_question") or {}).get(q.id)) if arm == "kp" \
                    else {"verdict": "n/a", "reason": "must_not_see is only checked on the KeyPulse arm"}
                method = "mechanical"
            elif r.get("exit") != 0 or r.get("is_error") or not (r.get("answer") or "").strip():
                v, method = error_verdict(f"run produced no answer (exit={r.get('exit')})"), "none"
            else:
                prompt = JUDGE_PROMPT.format(query=q.query, gold=q.gold_answer, expect=q.expect, answer=r["answer"])
                v, method = parse_judge_output(call_judge(prompt, model=model, claude=claude)), "llm"
            fh.write(json.dumps({"id": q.id, "arm": arm, "expect": q.expect, "method": method, **v},
                                ensure_ascii=False) + "\n")
            fh.flush()
            log(f"{q.id} {arm} {v['verdict']}")
    return out
