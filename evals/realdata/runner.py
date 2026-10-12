"""Run questions through Claude Code headless, one arm at a time. Resumable."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from evals.realdata.grants import make_grants
from evals.realdata.questions import Question

COMMON = (
    "You are answering a question about the user's own past activity on this Mac. "
    "Answer in Chinese, concisely. Cite where the answer came from. "
    "If you cannot find it, say 没有找到 and do not guess."
)
KP_PROMPT = COMMON + " Use only the keypulse MCP tools."
BASH_PROMPT = (
    COMMON + " Use shell and file tools to search local files (shell history, AI tool transcripts, "
    "browser data, documents, git repos, etc.). Do not use any KeyPulse database or KeyPulse tools."
)
INPUT_TRUNC = 300
BASE_FLAGS = ["--output-format", "stream-json", "--verbose", "--no-session-persistence",
              "--permission-mode", "dontAsk", "--strict-mcp-config"]


def build_command(arm: str, q: Question, *, model: str, max_turns: int, mcp_config: Path | None,
                  sandbox_profile: Path | None, sandbox_exec: str = "sandbox-exec", claude: str = "claude") -> list[str]:
    cmd = [claude, "-p", q.query, "--model", model, "--max-turns", str(max_turns), *BASE_FLAGS]
    if arm == "kp":
        assert mcp_config is not None
        cmd += ["--mcp-config", str(mcp_config), "--allowedTools", "mcp__keypulse__*",
                "--disallowedTools", "Bash", "Read", "Grep", "Glob", "WebFetch", "WebSearch", "Agent",
                "--append-system-prompt", KP_PROMPT]
        return cmd
    assert sandbox_profile is not None
    cmd += ["--allowedTools", "Bash", "Read", "Grep", "Glob",
            "--disallowedTools", "WebFetch", "WebSearch", "Agent", "--append-system-prompt", BASH_PROMPT]
    return [sandbox_exec, "-f", str(sandbox_profile), *cmd]


def parse_stream(stdout: str) -> dict:
    """Extract answer, cost and tool calls from stream-json output."""
    tools, answer, cost, is_error = [], "", None, False
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    inp = b.get("input")
                    tools.append({"name": b.get("name", ""),
                                  "input": json.dumps(inp, ensure_ascii=False)[:INPUT_TRUNC],
                                  # full args kept only for KeyPulse tools (small, needed for replay)
                                  **({"args": inp} if str(b.get("name", "")).startswith("mcp__keypulse__") else {})})
        elif ev.get("type") == "result":
            answer = ev.get("result") or ""
            cost = ev.get("total_cost_usd")
            is_error = bool(ev.get("is_error"))
    return {"answer": answer, "cost_usd": cost, "tools": tools, "is_error": is_error}


def done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            ids.add(json.loads(line)["id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return ids


def run_one(arm: str, q: Question, workdir: Path, *, model: str, max_turns: int, timeout: int,
            mcp_config: Path | None, sandbox_profile: Path | None, secrets: tuple[str, ...] = (),
            sandbox_exec: str = "sandbox-exec", claude: str = "claude") -> dict:
    cmd = build_command(arm, q, model=model, max_turns=max_turns, mcp_config=mcp_config,
                        sandbox_profile=sandbox_profile, sandbox_exec=sandbox_exec, claude=claude)
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        stdout, stderr, code = "", "timeout", -1
    dur = round(time.time() - t0, 1)
    parsed = parse_stream(stdout)
    for s in secrets:
        stderr = stderr.replace(s, "[REDACTED]")
    touched = arm == "bash" and any("keypulse" in t["input"].lower() for t in parsed["tools"])
    return {"id": q.id, "arm": arm, "group": q.group, "grant": list(q.grant), "query": q.query,
            "gold_answer": q.gold_answer, "expect": q.expect, "answer": parsed["answer"],
            "tool_calls": len(parsed["tools"]), "tools": parsed["tools"], "seconds": dur,
            "cost_usd": parsed["cost_usd"], "is_error": parsed["is_error"],
            "bash_touched_keypulse": touched, "exit": code, "stderr": stderr[-300:]}


def run_arm(arm: str, questions: list[Question], run_dir: Path, *, keypulse_bin: str, model: str = "sonnet",
            max_turns: int = 25, timeout: int = 900, sandbox_profile: Path | None = None,
            sandbox_exec: str = "sandbox-exec", claude: str = "claude", log=print) -> Path:
    out = run_dir / f"results-{arm}.jsonl"
    todo = [q for q in questions if q.id not in done_ids(out)]
    if arm == "bash":  # must_not_see is a grant-boundary check: only meaningful for KeyPulse
        todo = [q for q in todo if q.expect != "must_not_see"]
    if not todo:
        return out
    grants, db = {}, run_dir / "keypulse.db"
    if arm == "kp":
        grants = make_grants(run_dir / "home", db, {q.grant for q in todo}, "rdeval")
    with tempfile.TemporaryDirectory(prefix="rdeval-") as d, out.open("a", encoding="utf-8") as fh:
        work = Path(d)
        for q in todo:
            cfg, secrets = None, ()
            if arm == "kp":
                cid, token = grants[q.grant]
                cfg = work / f"mcp-{q.id}.json"
                cfg.write_text(json.dumps({"mcpServers": {"keypulse": {
                    "command": keypulse_bin, "args": ["mcp", "serve"],
                    "env": {"KEYPULSE_TOKEN": token, "KEYPULSE_CLIENT": cid, "HOME": str(run_dir / "home")}}}}))
                cfg.chmod(0o600)
                secrets = (token,)
            r = run_one(arm, q, work, model=model, max_turns=max_turns, timeout=timeout, mcp_config=cfg,
                        sandbox_profile=sandbox_profile, secrets=secrets, sandbox_exec=sandbox_exec, claude=claude)
            if cfg is not None:
                cfg.unlink(missing_ok=True)
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            log(f"{q.id} {arm} tools={r['tool_calls']} {r['seconds']}s exit={r['exit']}")
    return out


def which_or_die(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise SystemExit(f"{name!r} not found on PATH")
    return p
