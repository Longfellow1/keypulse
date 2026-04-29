from __future__ import annotations

import os

from keypulse.utils.env import load_dotenv_quiet


def test_load_dotenv_basic(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAZ", raising=False)
    n = load_dotenv_quiet(env)
    assert n == 2
    assert os.environ["FOO"] == "bar"
    assert os.environ["BAZ"] == "qux"


def test_load_dotenv_skips_comments_and_blank(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\n\nFOO=1\n# FOO=2\n", encoding="utf-8")
    monkeypatch.delenv("FOO", raising=False)
    n = load_dotenv_quiet(env)
    assert n == 1
    assert os.environ["FOO"] == "1"


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("FOO=from_file\n", encoding="utf-8")
    monkeypatch.setenv("FOO", "from_shell")
    n = load_dotenv_quiet(env)
    assert n == 0
    assert os.environ["FOO"] == "from_shell"


def test_load_dotenv_strips_quotes(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('FOO="quoted value"\nBAR=\'single\'\n', encoding="utf-8")
    monkeypatch.delenv("FOO", raising=False)
    monkeypatch.delenv("BAR", raising=False)
    load_dotenv_quiet(env)
    assert os.environ["FOO"] == "quoted value"
    assert os.environ["BAR"] == "single"


def test_load_dotenv_missing_file_returns_zero(tmp_path):
    n = load_dotenv_quiet(tmp_path / "nonexistent.env")
    assert n == 0


def test_load_dotenv_invalid_key_skipped(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("123BAD=x\n_GOOD=y\n=missing_key\n", encoding="utf-8")
    monkeypatch.delenv("_GOOD", raising=False)
    n = load_dotenv_quiet(env)
    assert n == 1
    assert os.environ["_GOOD"] == "y"
    assert "123BAD" not in os.environ
