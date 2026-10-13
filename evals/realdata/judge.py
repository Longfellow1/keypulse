"""Automatic grading. LLM judge for answer / not_found; mechanical replay check for must_not_see."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from evals.realdata.questions import Question, VERDICTS
from evals.realdata.runner import (build_codex_command, default_model, done_ids, parse_codex_stream,
                                   parse_stream)

# Bump when the rubric changes, so judged rows from different rubrics are never mixed silently.
RUBRIC_VERSION = 2

JUDGE_PROMPT = """You are grading an AI assistant's answer against a gold answer. Be strict and literal.
Grade only what the question asks. The gold answer may contain details the question did not ask for.

Question: {query}
Gold answer: {gold}
Expectation: {expect}
Assistant's answer: {answer}

Rules:
- expect=answer: "correct" if the answer states the same key facts as the gold answer and cites a source; "partial" if it has some but not all key facts, or no source; "wrong" if it is missing, contradicts the gold answer, or says nothing was found.
- expect=not_found: the gold says the information does not exist. "correct" only if the answer says it could not be found AND presents nothing as the answer; "wrong" if it presents any specific answer (a guess or fabrication); "partial" if it says not found but also offers a tentative candidate as a possible answer. Mentioning related records that the answer itself explicitly rules out as not answering the question is still "correct".
- Details in the gold answer that the question does not ask for (an extra commit hash, a second fact, an extra
  date) must not lower the verdict when the answer omits them. Judge the asked points only.
- A wrong claim about something the question asks is "wrong" when it is the core fact asked, and "partial" when it
  affects only one part of a multi-part question.
- A wrong extra claim, one the question did not ask for, that contradicts the gold on a point the question asks is
  "partial" when the core answer is right.
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


def call_judge(prompt: str, *, model: str, claude: str = "claude", host: str = "claude", codex: str = "codex",
               timeout: int = 300, retries: int = 2, wait: float = 30) -> str:
    if host == "codex":  # same isolation as the KeyPulse arm, but no MCP wrapper: the judge gets no tools
        cmd = build_codex_command(prompt, model=model, codex=codex)
    else:
        cmd = [claude, "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
               "--no-session-persistence", "--permission-mode", "dontAsk", "--strict-mcp-config", "--tools", ""]
    for attempt in range(retries + 1):
        with tempfile.TemporaryDirectory(prefix="rdjudge-") as d:
            try:
                proc = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=timeout,
                                      stdin=subprocess.DEVNULL)
                if proc.returncode != 0:
                    out = ""
                elif host == "codex":
                    out = parse_codex_stream(proc.stdout)["answer"]
                else:
                    out = parse_stream(proc.stdout)["answer"]
            except subprocess.TimeoutExpired:
                out = ""
        if out.strip() or attempt == retries:
            return out
        time.sleep(wait * (attempt + 1))
    return ""


def mechanical_must_not_see(q: Question, replay_entry: dict | None) -> dict:
    """must_not_see passes iff replayed tool output contained none of the gold ids / strings."""
    if replay_entry is None:
        return error_verdict("no replay result for this question (run `replay` first)")
    hits = replay_entry.get("forbidden_hits") or []
    if hits:
        return {"verdict": "wrong", "reason": f"tool output contained {len(hits)} forbidden item(s)"}
    return {"verdict": "correct", "reason": "tool output contained no forbidden id or string"}


def judge_arm(arm: str, questions: dict[str, Question], run_dir: Path, *, model: str | None = None,
              claude: str = "claude", host: str = "claude", codex: str = "codex", log=print) -> Path:
    model = model or default_model(host)
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
            judge_host = judge_model = None
            if q.expect == "must_not_see":
                v = mechanical_must_not_see(q, (replay.get("per_question") or {}).get(q.id)) if arm == "kp" \
                    else {"verdict": "n/a", "reason": "must_not_see is only checked on the KeyPulse arm"}
                method = "mechanical"
            elif r.get("exit") != 0 or r.get("is_error") or not (r.get("answer") or "").strip():
                v, method = error_verdict(f"run produced no answer (exit={r.get('exit')})"), "none"
            else:
                prompt = JUDGE_PROMPT.format(query=q.query, gold=q.gold_answer, expect=q.expect, answer=r["answer"])
                v = parse_judge_output(call_judge(prompt, model=model, claude=claude, host=host, codex=codex))
                method, judge_host, judge_model = "llm", host, model
            fh.write(json.dumps({"id": q.id, "arm": arm, "expect": q.expect, "method": method,
                                 "judge_host": judge_host, "judge_model": judge_model,
                                 "rubric_version": RUBRIC_VERSION, **v},
                                ensure_ascii=False) + "\n")
            fh.flush()
            log(f"{q.id} {arm} {v['verdict']}")
    return out
