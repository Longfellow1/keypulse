"""`keypulse mcp grant --host {claude,codex,cursor,generic}`: per-host connection config (Part A)."""
from __future__ import annotations

import json
import logging
import os
import tomllib

import pytest
from click.testing import CliRunner

from keypulse.config import Config
from keypulse.mcp import auth
from keypulse.mcp.cli import EGRESS_NOTICE, HOSTS, mcp_group
from keypulse.store import buckets
from keypulse.store.db import get_conn, init_db


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KEYPULSE_TOKEN", raising=False)
    monkeypatch.delenv("KEYPULSE_CLIENT", raising=False)
    buckets.reset_cache()
    init_db(Config.load().db_path_expanded)
    return h


def _grant(client: str, *extra: str):
    out = CliRunner().invoke(mcp_group, ["grant", client, *extra])
    assert out.exit_code == 0, out.output
    token = next(w for w in out.output.split() if w.startswith("kp_"))
    return out.output, token


def _json_block(output: str) -> dict:
    start = output.index("{")
    end = output.rindex("}") + 1
    return json.loads(output[start:end])


def test_default_host_is_claude_and_keeps_existing_structure(home) -> None:
    out, token = _grant("claude", "--buckets", "Work,Personal")
    assert f"-e KEYPULSE_TOKEN={token} -e KEYPULSE_CLIENT=claude --" in out
    assert _json_block(out)["mcpServers"]["keypulse"]["env"] == {"KEYPULSE_TOKEN": token, "KEYPULSE_CLIENT": "claude"}
    assert "Codex CLI" not in out and "~/.cursor/mcp.json" not in out
    assert EGRESS_NOTICE in out
    assert "keypulse mcp revoke claude" in out


def test_codex_prints_toml_block_with_absolute_path_and_alternative_command(home) -> None:
    out, token = _grant("codex", "--host", "codex")
    toml_start = out.index("[mcp_servers.keypulse]")
    toml_text = "\n".join(line for line in out[toml_start:].splitlines()[:5])
    parsed = tomllib.loads(toml_text)["mcp_servers"]["keypulse"]
    assert os.path.isabs(parsed["command"])
    assert parsed["args"] == ["mcp", "serve"]
    assert parsed["env"] == {"KEYPULSE_TOKEN": token, "KEYPULSE_CLIENT": "codex"}
    assert f"codex mcp add keypulse --env KEYPULSE_TOKEN={token} --env KEYPULSE_CLIENT=codex --" in out
    assert "HIST_IGNORE_SPACE" in out  # shell-history warning
    assert EGRESS_NOTICE in out
    assert "keypulse mcp revoke codex" in out


def test_cursor_prints_mcp_json_for_cursor_config(home) -> None:
    out, token = _grant("cursor", "--host", "cursor")
    assert "~/.cursor/mcp.json" in out
    server = _json_block(out)["mcpServers"]["keypulse"]
    assert server["args"] == ["mcp", "serve"]
    assert os.path.isabs(server["command"])
    assert server["env"] == {"KEYPULSE_TOKEN": token, "KEYPULSE_CLIENT": "cursor"}
    assert EGRESS_NOTICE in out
    assert "keypulse mcp revoke cursor" in out


def test_generic_prints_only_json_snippet(home) -> None:
    out, token = _grant("other", "--host", "generic")
    server = _json_block(out)["mcpServers"]["keypulse"]
    assert server["env"] == {"KEYPULSE_TOKEN": token, "KEYPULSE_CLIENT": "other"}
    assert "claude mcp add" not in out and "[mcp_servers" not in out
    assert EGRESS_NOTICE in out
    assert "keypulse mcp revoke other" in out


def test_every_host_is_listed_and_unknown_host_is_rejected(home) -> None:
    assert set(HOSTS) == {"claude", "codex", "cursor", "generic"}
    bad = CliRunner().invoke(mcp_group, ["grant", "x", "--host", "vscode"])
    assert bad.exit_code != 0


def test_token_is_not_written_to_logs(home, tmp_path) -> None:
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        out, token = _grant("codex", "--host", "codex")
    finally:
        root.removeHandler(handler)
    assert token in out
    assert all(token not in r.getMessage() for r in records)


def test_host_config_uses_running_executable_when_argv0_is_keypulse(tmp_path, monkeypatch) -> None:
    from keypulse.mcp import cli as mcp_cli

    exe = tmp_path / "keypulse"
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr("sys.argv", [str(exe), "mcp", "grant"])
    assert mcp_cli._keypulse_executable() == str(exe)


def test_stored_grant_never_contains_the_plain_token(home) -> None:
    _, token = _grant("claude")
    assert auth.authenticate(token) is not None
    rows = get_conn().execute("SELECT * FROM access_grants").fetchall()
    assert token not in json.dumps([dict(r) for r in rows], default=str)
