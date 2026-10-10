"""P0-06: Codex rollouts + Git commits through the same mainline as Claude Code (AT03, AT08)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from keypulse.mcp import auth
from keypulse.sources import registry
from keypulse.sources.cleaning.config import CleaningConfig
from keypulse.sources.plugins.claude_code import ClaudeCodeSource
from keypulse.sources.plugins.codex_cli import CodexCliSource
from keypulse.sources.plugins.git_log import GitLogSource
from keypulse.sources.sink import flush_named_entity_backfill
from keypulse.store.db import close, get_conn, init_db
from mcp_helpers import server_for

SID = "0199aaaa-1111-2222-3333-444455556666"
FAKE_KEY = "sk-test-FAKE0000000000000000000000"


def _rec(ordinal: int, ts: str, rtype: str, payload: dict) -> str:
    return json.dumps({"timestamp": ts, "ordinal": ordinal, "type": rtype, "payload": payload})


def _msg(role: str, kind: str, *texts: str) -> dict:
    return {"type": "message", "role": role, "content": [{"type": kind, "text": t} for t in texts]}


def _rollout_lines() -> list[str]:
    """Current-shape rollout: meta, developer prompt, injected context, real turns, tool noise."""
    return [
        _rec(1, "2026-05-01T08:00:00.000Z", "session_meta", {"session_id": SID, "id": SID, "cwd": "/work/demo-app", "cli_version": "0.0.0"}),
        _rec(2, "2026-05-01T08:00:01.000Z", "response_item", _msg("developer", "input_text", "developer sandbox rules")),
        _rec(3, "2026-05-01T08:00:01.500Z", "response_item", _msg("user", "input_text", "# AGENTS.md instructions for /work/demo-app\n\n<INSTRUCTIONS>be nice</INSTRUCTIONS>")),
        _rec(4, "2026-05-01T08:00:02.000Z", "response_item", _msg("user", "input_text", "<environment_context>\n<cwd>/work/demo-app</cwd>\n</environment_context>")),
        _rec(5, "2026-05-01T08:00:03.000Z", "turn_context", {"cwd": "/work/demo-app/sub", "model": "x"}),
        _rec(6, "2026-05-01T08:00:04.000Z", "response_item", _msg("user", "input_text", "# Plan\nswitch the narwhalquux cache to LRU", "<image name=[Image #1]>")),
        _rec(7, "2026-05-01T08:00:05.000Z", "response_item", {"type": "reasoning", "summary": []}),
        _rec(8, "2026-05-01T08:00:06.000Z", "response_item", {"type": "custom_tool_call", "name": "shell", "input": "ls"}),
        _rec(9, "2026-05-01T08:00:07.000Z", "response_item", {"type": "custom_tool_call_output", "output": "narwhalquux in tool output"}),
        _rec(10, "2026-05-01T08:00:08.000Z", "event_msg", {"type": "user_message", "message": "narwhalquux duplicate"}),
        _rec(11, "2026-05-01T08:00:09.000Z", "response_item", _msg("assistant", "output_text", "Done: the narwhalquux cache now evicts LRU entries.")),
        _rec(12, "2026-05-01T08:00:10.000Z", "compacted", {"message": "narwhalquux summary", "replacement_history": []}),
        _rec(13, "2026-05-01T08:00:11.000Z", "future_record_type", {"message": "unknown"}),
        _rec(14, "2026-05-01T08:00:12.000Z", "response_item", _msg("user", "input_text", f"my key is api_key={FAKE_KEY} keep narwhalquux private")),
    ]


def _write_rollout(root: Path, lines: list[str], day: str = "2026/05/01", sid: str = SID) -> Path:
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-{day.replace('/', '-')}T08-00-00-{sid}.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def _read_all(source: str | None = None) -> int:
    now = datetime.now(timezone.utc)
    n = sum(1 for _ in registry.read_all(datetime(2000, 1, 1, tzinfo=timezone.utc), now, source=source))
    flush_named_entity_backfill()
    return n


def _count(source: str | None = None) -> int:
    if source:
        return get_conn().execute("SELECT count(*) FROM raw_events WHERE source = ?", (source,)).fetchone()[0]
    return get_conn().execute("SELECT count(*) FROM raw_events").fetchone()[0]


def _git(repo: Path, *args: str, when: str | None = None) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", *args],
                   cwd=repo, check=True, capture_output=True, env=env)


def _commit(repo: Path, name: str, message: str, when: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", message, when=when)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    sessions = tmp_path / "codex" / "sessions"
    rollout = _write_rollout(sessions, _rollout_lines())

    repos = tmp_path / "repos"
    repo = repos / "demo-app"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "a.txt", "fix: narwhalquux cache eviction order", "2026-05-01T09:00:00+00:00")

    proj = tmp_path / "claude" / "projects" / "-work-demo-app"
    proj.mkdir(parents=True)
    claude = proj / "s1.jsonl"
    claude.write_text(
        json.dumps({"type": "user", "uuid": "u1", "sessionId": "s1", "timestamp": "2026-05-01T07:00:00Z",
                    "message": {"role": "user", "content": [{"type": "text", "text": "why is the narwhalquux cache slow?"}]}}) + "\n",
        encoding="utf-8",
    )

    def make() -> dict:
        return {
            "claude_code": ClaudeCodeSource(projects_root=tmp_path / "claude" / "projects", self_exclude_dirs=set(), use_cursor=True),
            "codex_cli": CodexCliSource(sessions_root=sessions, use_cursor=True),
            "git_log": GitLogSource(search_roots=[repos], cwd=tmp_path, use_cursor=True),
        }

    monkeypatch.setattr(registry, "_PLUGINS", make())
    monkeypatch.setattr(registry, "load_cleaning_config", lambda: CleaningConfig())
    db_path = tmp_path / "keypulse.db"
    init_db(db_path)
    yield type("Env", (), {"tmp": tmp_path, "sessions": sessions, "rollout": rollout, "repo": repo, "db": db_path,
                           "claude": claude, "make": staticmethod(make)})
    close()


def _search(token: str, **args) -> dict:
    server = server_for(token)

    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return await client.call_tool("search_context", args)

    return json.loads(asyncio.run(run()).content[0].text)


# ── AT03: one topic, three sources, each with its own evidence ────────────────

def test_at03_same_topic_returns_claude_codex_and_git_with_own_evidence(env) -> None:
    _read_all()
    token = auth.create_grant("claude")
    data = _search(token, keywords=["narwhalquux"])
    assert data["status"] == "ok"
    by_source: dict[str, list[dict]] = {}
    for r in data["results"]:
        by_source.setdefault(r["source"], []).append(r)
    assert set(by_source) == {"claude_code", "codex_cli", "git_log"}
    conn = get_conn()
    locators = {}
    for source, rows in by_source.items():
        assert all(r["bucket"] == "Work" for r in rows)
        ev = conn.execute("SELECT locator, offset_or_line FROM evidence WHERE id = ?",
                          (int(rows[0]["evidence_id"].removeprefix("evd_")),)).fetchone()
        locators[source] = ev["locator"]
    assert locators["claude_code"] == str(env.claude.resolve())
    assert locators["codex_cli"] == str(env.rollout.resolve())
    full_hash = conn.execute("SELECT external_id FROM raw_events WHERE source = 'git_log'").fetchone()[0]
    assert len(full_hash) == 40
    assert locators["git_log"] == f"{env.repo.resolve()}@{full_hash}"


def test_codex_keeps_only_real_turns_and_secrets_are_redacted(env) -> None:
    _read_all("codex_cli")
    rows = get_conn().execute("SELECT external_id, speaker, content_text, ts_start, kind FROM raw_events WHERE source = 'codex_cli' ORDER BY ts_start").fetchall()
    # ordinals 6 (user prompt; image/AGENTS/env parts dropped), 11 (assistant), 14 (user)
    assert [r["external_id"] for r in rows] == [f"{SID}:6", f"{SID}:11", f"{SID}:14"]
    assert rows[0]["content_text"] == "# Plan\nswitch the narwhalquux cache to LRU"  # real '#' text kept
    assert rows[0]["ts_start"] == "2026-05-01T08:00:04+00:00" and rows[0]["kind"] == "message"
    assert "FAKE0000" not in rows[2]["content_text"] and "[REDACTED]" in rows[2]["content_text"]
    meta = json.loads(get_conn().execute("SELECT metadata_json FROM raw_events WHERE external_id = ?", (f"{SID}:6",)).fetchone()[0])
    assert meta["cwd"] == "/work/demo-app/sub" and meta["project"] == "sub" and meta["session_id"] == SID
    ev_line = get_conn().execute("SELECT offset_or_line FROM evidence e JOIN raw_events r ON r.id = e.event_id WHERE r.external_id = ?", (f"{SID}:6",)).fetchone()[0]
    assert ev_line == 6


# ── AT08: idempotent import, incremental append ───────────────────────────────

def test_at08_rerun_no_duplicates_and_append_only_ingests_new(env) -> None:
    assert _read_all() == 5  # 1 claude + 3 codex + 1 commit
    assert _read_all() == 0
    assert _count() == 5

    with env.rollout.open("a", encoding="utf-8") as fh:
        fh.write(_rec(15, "2026-05-02T08:00:00.000Z", "response_item", _msg("assistant", "output_text", "follow-up narwhalquux note")) + "\n")
    _commit(env.repo, "b.txt", "feat: second narwhalquux commit", "2026-05-02T09:00:00+00:00")
    assert _read_all() == 2
    assert _count("codex_cli") == 4 and _count("git_log") == 2
    assert _read_all() == 0


def test_lost_cursor_reimport_dedups_by_external_id(env) -> None:
    _read_all()
    get_conn().execute("DELETE FROM sync_cursors")
    get_conn().commit()
    registry._PLUGINS.update(env.make())
    _read_all()
    assert _count() == 5
    assert get_conn().execute("SELECT count(*) FROM evidence").fetchone()[0] == 5


def test_cursor_survives_restart_and_not_advanced_on_persist_failure(env, monkeypatch) -> None:
    real_persist = registry.persist_semantic_event
    monkeypatch.setattr(registry, "persist_semantic_event", lambda e: (_ for _ in ()).throw(RuntimeError("disk full")))
    _read_all()
    assert _count() == 0
    monkeypatch.setattr(registry, "persist_semantic_event", real_persist)
    registry._PLUGINS.update(env.make())
    assert _read_all() == 5
    close()
    init_db(env.db)
    registry._PLUGINS.update(env.make())
    assert _read_all() == 0
    cur = json.loads(get_conn().execute("SELECT cursor FROM sync_cursors s JOIN sources o ON o.id = s.source_id WHERE o.type = 'codex_cli'").fetchone()[0])
    state = cur["files"][env.rollout.relative_to(env.sessions).as_posix()]
    assert state["offset"] == env.rollout.stat().st_size and state["sid"] == SID


def test_partial_trailing_line_waits_until_complete(env) -> None:
    _read_all()
    line = _rec(16, "2026-05-02T10:00:00.000Z", "response_item", _msg("user", "input_text", "half written narwhalquux"))
    with env.rollout.open("a", encoding="utf-8") as fh:
        fh.write(line[:40])
    assert _read_all("codex_cli") == 0
    with env.rollout.open("a", encoding="utf-8") as fh:
        fh.write(line[40:] + "\n")
    assert _read_all("codex_cli") == 1


def test_truncated_or_rotated_file_restarts_from_zero_without_duplicates(env) -> None:
    _read_all("codex_cli")
    env.rollout.write_text("\n".join(_rollout_lines()[:6]) + "\n", encoding="utf-8")  # shrink
    _read_all("codex_cli")
    assert _count("codex_cli") == 3


# ── robustness ────────────────────────────────────────────────────────────────

def test_sessions_found_recursively_and_old_shape_supported(env) -> None:
    old_sid = "0188bbbb-0000-0000-0000-000000000001"
    old = [
        json.dumps({"id": old_sid, "timestamp": "2025-01-02T03:04:05.000Z", "instructions": "x"}),
        json.dumps({"record_type": "state"}),
        json.dumps({"type": "message", "role": "user", "content": [{"type": "input_text", "text": "legacy shape zebrafish prompt"}]}),
        json.dumps({"type": "reasoning", "summary": []}),
        json.dumps({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "legacy zebrafish answer"}]}),
        "{not json",
    ]
    _write_rollout(env.sessions, old, day="2025/01/02/deep/er", sid=old_sid)
    _read_all("codex_cli")
    rows = get_conn().execute("SELECT external_id, speaker, ts_start FROM raw_events WHERE content_text LIKE '%zebrafish%' ORDER BY id").fetchall()
    assert [r["external_id"] for r in rows] == [f"{old_sid}:L3", f"{old_sid}:L5"]
    assert rows[0]["ts_start"] == "2025-01-02T03:04:05+00:00" and rows[0]["speaker"] == "user"


def test_one_failing_source_or_file_does_not_stop_the_others(env, monkeypatch) -> None:
    class Boom(CodexCliSource):
        def read(self, instance, since, until):
            raise RuntimeError("parser exploded")

    registry._PLUGINS["codex_cli"] = Boom(sessions_root=env.sessions, use_cursor=True)
    assert _read_all() == 2  # claude + git still ingested
    assert _count("codex_cli") == 0

    # a corrupt file (bad bytes, not JSON) next to a good one: good one still ingested
    registry._PLUGINS["codex_cli"] = CodexCliSource(sessions_root=env.sessions, use_cursor=True)
    (env.sessions / "2026" / "05" / "01" / "rollout-junk.jsonl").write_bytes(b"\xff\xfe\x00 not json\n" * 3)
    assert _read_all("codex_cli") == 3


def test_missing_codex_dir_discovers_nothing(tmp_path: Path) -> None:
    assert CodexCliSource(sessions_root=tmp_path / "nope", history_path=tmp_path / "nope.jsonl").discover() == []


def test_git_cursor_overlap_does_not_duplicate_and_bucket_default_is_work(env) -> None:
    _read_all("git_log")
    _read_all("git_log")
    rows = get_conn().execute("SELECT bucket, kind, ts_start FROM raw_events WHERE source = 'git_log'").fetchall()
    assert len(rows) == 1 and rows[0]["bucket"] == "Work" and rows[0]["kind"] == "commit"
    assert rows[0]["ts_start"] == "2026-05-01T09:00:00+00:00"  # commit time


def test_codex_automation_heartbeat_is_dropped_for_both_roles() -> None:
    from keypulse.sources.plugins.codex_cli import _message_text

    beat = [{"type": "output_text", "text": "<heartbeat>\n  <automation_id>codex-5</automation_id>\n</heartbeat>"}]
    assert _message_text(beat, "assistant") == ""
    assert _message_text([{"type": "input_text", "text": beat[0]["text"]}], "user") == ""
    assert _message_text([{"type": "output_text", "text": "real reply"}], "assistant") == "real reply"
