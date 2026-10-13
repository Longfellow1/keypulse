"""Run questions through Claude Code (default) or Codex CLI headless, one arm at a time. Resumable."""
from __future__ import annotations

import json
import os
import re
import shlex
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

HOSTS = ("claude", "codex")
DEFAULT_MODEL = {"claude": "sonnet", "codex": "gpt-6-luna"}  # cheap model for codex
CODEX_BIN = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"
# Built-in tools switched off for Codex runs: no shell, files, apps, browser, or sub-agents. MCP stays on.
CODEX_DISABLE = ["shell_tool", "unified_exec", "apps", "plugins", "browser_use", "in_app_browser",
                 "computer_use", "image_generation", "hooks", "skill_search", "tool_suggest", "multi_agent"]
# Codex errors that say the API was busy, not that the arm did something wrong.
CODEX_TRANSIENT = re.compile(r"rate.?limit|429|\b5\d\d\b|stream disconnected|overloaded", re.IGNORECASE)


def default_model(host: str) -> str:
    return DEFAULT_MODEL[host]


def check_host(arm: str, host: str) -> None:
    if host not in HOSTS:
        raise ValueError(f"unknown host {host!r}")
    if host == "codex" and arm != "kp":
        raise ValueError("--host codex runs only the KeyPulse arm; the bash arm stays on Claude (--host claude)")


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


def build_codex_command(prompt: str, *, model: str, codex: str = "codex", wrapper: Path | None = None) -> list[str]:
    """Isolated, tool-less `codex exec`. With `wrapper`, the KeyPulse MCP server is started through it.
    The token lives only inside the wrapper file, never in argv."""
    cmd = [codex, "exec", "--json", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
           "-s", "read-only", "-m", model]
    for feature in CODEX_DISABLE:
        cmd += ["--disable", feature]
    cmd += ["-c", 'web_search="disabled"']
    if wrapper is not None:
        cmd += ["-c", f"mcp_servers.keypulse.command={json.dumps(str(wrapper))}",
                # exec runs with approval_policy=never: without this every MCP call is refused
                "-c", 'mcp_servers.keypulse.default_tools_approval_mode="approve"']
    return [*cmd, prompt]


def codex_kp_prompt(query: str) -> str:
    """Codex has no --append-system-prompt, so the instructions go in front of the question."""
    return f"{KP_PROMPT}\n\nQuestion: {query}"


def write_codex_wrapper(path: Path, *, keypulse_bin: str, token: str, client: str, home: Path) -> None:
    """0700 shell script that exports the grant and execs `keypulse mcp serve`. Deleted after each question."""
    body = "\n".join([
        "#!/bin/sh",
        f"export KEYPULSE_TOKEN={shlex.quote(token)}",
        f"export KEYPULSE_CLIENT={shlex.quote(client)}",
        f"export HOME={shlex.quote(str(home))}",
        f"exec {shlex.quote(keypulse_bin)} mcp serve",
        "",
    ])
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)  # never world-readable, even briefly
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    path.chmod(0o700)


RETRIES, RETRY_WAIT = 2, 30  # transient API failures (seconds, linear backoff)


def parse_stream(stdout: str) -> dict:
    """Extract answer, cost and tool calls from stream-json output."""
    tools, answer, cost, is_error, error = [], "", None, False, ""
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
            is_error = bool(ev.get("is_error"))
            answer = "" if is_error else (ev.get("result") or "")
            cost = ev.get("total_cost_usd")
            if is_error:
                error = str(ev.get("result") or ev.get("subtype") or "error")[:200]
    return {"answer": answer, "cost_usd": cost, "tools": tools, "is_error": is_error, "error": error}


def _codex_args(raw) -> dict:
    """MCP arguments arrive as a dict or as a JSON string."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return {"_unparsed": str(raw)[:INPUT_TRUNC]}


def _error_text(v) -> str:
    if isinstance(v, dict):
        v = v.get("message")
    return str(v or "error")[:200]


def parse_codex_stream(stdout: str) -> dict:
    """Same keys as parse_stream, read from `codex exec --json` events. The answer is the last agent message.
    Anything other than KeyPulse MCP calls and messages is recorded in non_kp_tools (an isolation violation)."""
    tools, messages, non_kp, usage = [], [], [], None
    is_error, error = False, ""
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "item.completed":
            item = ev.get("item")
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "agent_message":
                messages.append(str(item.get("text") or ""))
            elif itype == "mcp_tool_call":
                if item.get("server") != "keypulse":
                    non_kp.append(f"mcp_tool_call:{item.get('server')}")
                    continue
                args = _codex_args(item.get("arguments"))
                tools.append({"name": f"mcp__keypulse__{item.get('tool', '')}",
                              "input": json.dumps(args, ensure_ascii=False)[:INPUT_TRUNC], "args": args})
            elif itype in ("reasoning", "error"):
                continue
            elif itype:
                non_kp.append(str(itype))
        elif kind == "turn.completed":
            if isinstance(ev.get("usage"), dict):
                usage = ev["usage"]
        elif kind == "turn.failed":
            is_error, error = True, _error_text(ev.get("error"))
        elif kind == "error":
            is_error, error = True, _error_text(ev.get("message") or ev.get("error"))
    answer = "" if is_error else (messages[-1] if messages else "")
    return {"answer": answer, "cost_usd": None, "tools": tools, "is_error": is_error, "error": error,
            "usage": usage, "non_kp_tools": non_kp}


def is_transient(row: dict) -> bool:
    """A row that failed before doing anything (API/usage error, crash): not an answer, so it is retried on
    resume instead of being graded. API errors count as transient even after tool calls; other runs that used
    tools and then failed are kept and graded as they are."""
    if "verdict" in row:
        return row.get("verdict") == "error" and row.get("method") in ("llm", "none")
    if not (row.get("exit") != 0 or row.get("is_error")):
        return False
    # older rows stored the CLI's error text as the answer
    text = f"{row.get('error') or ''} {row.get('answer') or ''}".strip()
    if text.startswith("API Error") or row.get("stderr") == "timeout" and not row.get("tool_calls"):
        return True  # connection / overload / rate limit: says nothing about the arm, even mid-run
    if row.get("host") == "codex" and CODEX_TRANSIENT.search(str(row.get("error") or "")):
        return True  # codex rate limit / 5xx / dropped stream
    return not row.get("tool_calls") and not (row.get("answer") or "").strip()


def done_ids(path: Path) -> set[str]:
    """Ids already done. Transient failures are dropped from the file so a resume reruns them."""
    if not path.exists():
        return set()
    keep, ids = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or "id" not in row or is_transient(row):
            continue
        keep.append(line)
        ids.add(row["id"])
    path.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
    return ids


def run_one(arm: str, q: Question, workdir: Path, *, model: str, max_turns: int, timeout: int,
            mcp_config: Path | None, sandbox_profile: Path | None, secrets: tuple[str, ...] = (),
            sandbox_exec: str = "sandbox-exec", claude: str = "claude", host: str = "claude",
            codex: str = "codex") -> dict:
    """mcp_config is the MCP json for claude hosts and the wrapper script for codex hosts."""
    check_host(arm, host)
    if host == "codex":
        cmd = build_codex_command(codex_kp_prompt(q.query), model=model, codex=codex, wrapper=mcp_config)
    else:
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
    parsed = parse_codex_stream(stdout) if host == "codex" else parse_stream(stdout)
    for s in secrets:
        stderr = stderr.replace(s, "[REDACTED]")
    touched = arm == "bash" and any("keypulse" in t["input"].lower() for t in parsed["tools"])
    row = {"id": q.id, "arm": arm, "host": host, "model": model, "group": q.group, "grant": list(q.grant),
           "query": q.query, "gold_answer": q.gold_answer, "expect": q.expect, "answer": parsed["answer"],
           "tool_calls": len(parsed["tools"]), "tools": parsed["tools"], "seconds": dur,
           "cost_usd": parsed["cost_usd"], "is_error": parsed["is_error"], "error": parsed["error"],
           "bash_touched_keypulse": touched, "exit": code, "stderr": stderr[-300:]}
    if host == "codex":
        row["usage"] = parsed["usage"]
        row["non_kp_tools"] = parsed["non_kp_tools"]
    return row


def run_arm(arm: str, questions: list[Question], run_dir: Path, *, keypulse_bin: str, model: str | None = None,
            max_turns: int = 25, timeout: int = 900, sandbox_profile: Path | None = None,
            sandbox_exec: str = "sandbox-exec", claude: str = "claude", host: str = "claude",
            codex: str = "codex", log=print) -> Path:
    check_host(arm, host)
    model = model or default_model(host)
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
                if host == "codex":
                    cfg = work / f"kp-{q.id}.sh"
                    write_codex_wrapper(cfg, keypulse_bin=keypulse_bin, token=token, client=cid,
                                        home=run_dir / "home")
                else:
                    cfg = work / f"mcp-{q.id}.json"
                    cfg.write_text(json.dumps({"mcpServers": {"keypulse": {
                        "command": keypulse_bin, "args": ["mcp", "serve"],
                        "env": {"KEYPULSE_TOKEN": token, "KEYPULSE_CLIENT": cid, "HOME": str(run_dir / "home")}}}}))
                    cfg.chmod(0o600)
                secrets = (token,)
            for attempt in range(RETRIES + 1):
                r = run_one(arm, q, work, model=model, max_turns=max_turns, timeout=timeout, mcp_config=cfg,
                            sandbox_profile=sandbox_profile, secrets=secrets, sandbox_exec=sandbox_exec,
                            claude=claude, host=host, codex=codex)
                if not is_transient(r) or attempt == RETRIES:
                    break
                log(f"{q.id} {arm} transient failure ({r.get('error') or r.get('exit')}), retrying")
                time.sleep(RETRY_WAIT * (attempt + 1))
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
