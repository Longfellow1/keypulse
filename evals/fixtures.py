"""Materialise the synthetic fixture events into per-plugin on-disk formats.

Every path lives under the caller-supplied ``root`` (a temp dir); plugins are built
with explicit roots so the real HOME / ~/.claude / ~/.codex / browser profiles are
never touched.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from evals.fixture_data import EVENTS, FxEvent

_WEBKIT_EPOCH_OFFSET_US = 11_644_473_600 * 1_000_000
_CF_EPOCH_OFFSET_S = 978_307_200


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def _iso_z(ts: str) -> str:
    return ts + "Z"


def _by_source() -> dict[str, list[FxEvent]]:
    out: dict[str, list[FxEvent]] = defaultdict(list)
    for ev in EVENTS:
        out[ev.source].append(ev)
    return out


def _write_claude_code(root: Path, events: list[FxEvent]) -> Path:
    projects = root / "claude" / "projects"
    by_project: dict[str, list[FxEvent]] = defaultdict(list)
    for ev in events:
        by_project[ev.group].append(ev)
    for project, evs in by_project.items():
        d = projects / project
        d.mkdir(parents=True)
        lines = []
        for ev in sorted(evs, key=lambda e: e.ts):
            lines.append(
                json.dumps(
                    {
                        "type": ev.actor,
                        "uuid": ev.id,
                        "sessionId": f"sess-{project}",
                        "timestamp": _iso_z(ev.ts),
                        "message": {"role": ev.actor, "content": [{"type": "text", "text": ev.text}]},
                    },
                    ensure_ascii=False,
                )
            )
        (d / f"sess-{project}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return projects


def _write_codex(root: Path, events: list[FxEvent]) -> Path:
    """Rollout tree (sessions/YYYY/MM/DD/rollout-*.jsonl) in the current Codex shape, with noise records."""
    sessions = root / "codex" / "sessions"
    by_session: dict[str, list[FxEvent]] = defaultdict(list)
    for ev in events:
        by_session[ev.group].append(ev)
    for sid, evs in by_session.items():
        evs = sorted(evs, key=lambda e: e.ts)
        day = _dt(evs[0].ts)
        d = sessions / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        d.mkdir(parents=True, exist_ok=True)
        counter = [0]

        def rec(ts: str, rtype: str, payload: dict) -> str:
            counter[0] += 1
            return json.dumps(
                {"timestamp": _iso_z(ts), "ordinal": counter[0], "type": rtype, "payload": payload}, ensure_ascii=False
            )

        def msg(role: str, kind: str, text: str) -> dict:
            return {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}

        lines = [
            rec(evs[0].ts, "session_meta", {"session_id": sid, "id": sid, "timestamp": _iso_z(evs[0].ts), "cwd": f"/tmp/proj/{sid}"}),
            rec(evs[0].ts, "response_item", msg("developer", "input_text", "<permissions instructions> sandbox")),
            rec(evs[0].ts, "response_item", msg("user", "input_text", "<environment_context><cwd>/tmp</cwd></environment_context>")),
        ]
        for ev in evs:
            lines.append(rec(ev.ts, "turn_context", {"cwd": f"/tmp/proj/{sid}"}))
            lines.append(rec(ev.ts, "response_item", {"type": "reasoning", "summary": []}))
            kind = "output_text" if ev.actor == "assistant" else "input_text"
            lines.append(rec(ev.ts, "response_item", msg(ev.actor, kind, ev.text)))
            lines.append(rec(ev.ts, "response_item", {"type": "custom_tool_call_output", "output": "ok"}))
        (d / f"rollout-{day:%Y-%m-%dT%H-%M-%S}-{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sessions


def _write_zsh(root: Path, events: list[FxEvent]) -> Path:
    path = root / "zsh" / ".zsh_history"
    path.parent.mkdir(parents=True)
    with path.open("wb") as fh:
        for ev in sorted(events, key=lambda e: e.ts):
            fh.write(f": {int(_dt(ev.ts).timestamp())}:2;{ev.text}\n".encode("utf-8"))
    return path


def _write_chrome(root: Path, events: list[FxEvent]) -> Path:
    profiles = root / "chrome"
    for profile in sorted({e.group for e in events}):
        db = profiles / profile / "History"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        try:
            conn.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")
            conn.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER)")
            for i, ev in enumerate((e for e in events if e.group == profile), start=1):
                conn.execute("INSERT INTO urls(id, url, title) VALUES (?, ?, ?)", (i, ev.url, ev.text))
                conn.execute(
                    "INSERT INTO visits(id, url, visit_time) VALUES (?, ?, ?)",
                    (i, i, int(_dt(ev.ts).timestamp() * 1_000_000) + _WEBKIT_EPOCH_OFFSET_US),
                )
            conn.commit()
        finally:
            conn.close()
    return profiles


def _write_safari(root: Path, events: list[FxEvent]) -> Path:
    db = root / "safari" / "History.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
        conn.execute(
            "CREATE TABLE history_visits (id INTEGER PRIMARY KEY, history_item INTEGER, title TEXT, visit_time REAL)"
        )
        for i, ev in enumerate(events, start=1):
            conn.execute("INSERT INTO history_items(id, url) VALUES (?, ?)", (i, ev.url))
            conn.execute(
                "INSERT INTO history_visits(id, history_item, title, visit_time) VALUES (?, ?, ?, ?)",
                (i, i, ev.text, _dt(ev.ts).timestamp() - _CF_EPOCH_OFFSET_S),
            )
        conn.commit()
    finally:
        conn.close()
    return db


def _write_git(root: Path, events: list[FxEvent]) -> Path:
    repos = root / "repos"
    env_base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(root / "githome"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    (root / "githome").mkdir(exist_ok=True)
    for repo_name in sorted({e.group for e in events}):
        repo = repos / repo_name
        repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env=env_base, capture_output=True)
        # git_log ingests only the repo owner's commits; the synthetic owner is the eval tester.
        for key, value in (("user.name", "Eval Tester"), ("user.email", "eval@example.invalid")):
            subprocess.run(["git", "config", key, value], cwd=repo, check=True, env=env_base, capture_output=True)
        for n, ev in enumerate((e for e in sorted(events, key=lambda e: e.ts) if e.group == repo_name), start=1):
            (repo / f"f{n}.txt").write_text(str(n), encoding="utf-8")
            when = _dt(ev.ts).isoformat()
            env = {**env_base, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
            subprocess.run(["git", "add", "."], cwd=repo, check=True, env=env, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Eval Tester", "-c", "user.email=eval@example.invalid",
                 "commit", "-q", "-m", ev.text],
                cwd=repo, check=True, env=env, capture_output=True,
            )
    return repos


def _write_vault(root: Path, events: list[FxEvent]) -> Path:
    vault = root / "vault" / "Knowledge"
    (vault / ".obsidian").mkdir(parents=True)
    for n, ev in enumerate(events, start=1):
        note = vault / ev.group / f"note-{n:02d}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(f"# {ev.text}\n\nBody text is not indexed by the plugin.\n", encoding="utf-8")
        ts = _dt(ev.ts).timestamp()
        os.utime(note, (ts, ts))
    return vault


def build_plugins(root: Path) -> dict[str, object]:
    """Write all fixtures under ``root`` and return ready-to-register plugin instances."""
    from penpal.sources.approval import ApprovalStore
    from penpal.sources.plugins.chrome_history import ChromeHistorySource
    from penpal.sources.plugins.claude_code import ClaudeCodeSource
    from penpal.sources.plugins.codex_cli import CodexCliSource
    from penpal.sources.plugins.git_log import GitLogSource
    from penpal.sources.plugins.markdown_vault import MarkdownVaultSource
    from penpal.sources.plugins.safari_history import SafariHistorySource
    from penpal.sources.plugins.zsh_history import ZshHistorySource

    grouped = _by_source()
    return {
        "claude_code": ClaudeCodeSource(
            projects_root=_write_claude_code(root, grouped["claude_code"]), self_exclude_dirs=set(), use_cursor=True
        ),
        "codex_cli": CodexCliSource(sessions_root=_write_codex(root, grouped["codex_cli"]), use_cursor=True),
        "git_log": GitLogSource(search_roots=[_write_git(root, grouped["git_log"])], cwd=root, use_cursor=True),
        "zsh_history": ZshHistorySource(history_path=_write_zsh(root, grouped["zsh_history"])),
        "chrome_history": ChromeHistorySource(profiles_root=_write_chrome(root, grouped["chrome_history"])),
        "safari_history": SafariHistorySource(history_path=_write_safari(root, grouped["safari_history"])),
        "markdown_vault": MarkdownVaultSource(
            roots=[_write_vault(root, grouped["markdown_vault"])],
            approval_store=ApprovalStore(path=root / "sources-approval.json"),
        ),
    }
