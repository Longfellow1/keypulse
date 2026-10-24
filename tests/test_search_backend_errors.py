from __future__ import annotations

import logging
import sqlite3

import pytest
from click.testing import CliRunner

from penpal.mcp.auth import Grant
from penpal.mcp import tools as mcp_tools
from penpal.search import backends


def test_backend_logs_and_propagates_sqlite_errors_without_query_text(monkeypatch, caplog) -> None:
    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("database failure with private query fragment")

    monkeypatch.setattr(backends, "get_conn", lambda: BrokenConnection())

    with caplog.at_level(logging.ERROR, logger="penpal.search.backends"):
        with pytest.raises(sqlite3.OperationalError, match="private query fragment"):
            backends.FtsSearchBackend().search("private query fragment")

    assert "OperationalError" in caplog.text
    assert "private query fragment" not in caplog.text


def test_mcp_search_returns_existing_error_shape_for_sqlite_failure(monkeypatch) -> None:
    grant = Grant(id=1, client_id="codex", buckets=("Work",), operations=("read",))

    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("internal sqlite detail")

    monkeypatch.setattr(mcp_tools, "search_events", fail)

    result = mcp_tools._search(grant, ["release"], None, None, 10)

    assert result == {"status": "error", "message": "search failed"}


def test_cli_search_reports_database_failure_without_query_or_sqlite_details(monkeypatch) -> None:
    import penpal.cli as cli

    class BrokenBackend:
        def search(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("internal sqlite detail")

    monkeypatch.setattr(cli, "get_config", lambda: object())
    monkeypatch.setattr(cli, "require_db", lambda _cfg: None)
    monkeypatch.setattr(cli, "resolve_search_backend", lambda: BrokenBackend())

    result = CliRunner().invoke(cli.main, ["search", "sensitive query"])

    assert result.exit_code != 0
    from penpal.i18n import T

    assert T("搜索失败", "Search failed") in result.output
    assert "sensitive query" not in result.output
    assert "internal sqlite detail" not in result.output
