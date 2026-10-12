from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from keypulse.sources.plugins.git_log import GitLogSource
from keypulse.sources.types import DataSourceInstance


def _run_git(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "sample-repo"
    repo.mkdir()

    _run_git(["init"], cwd=repo)
    _run_git(["config", "user.name", "KeyPulse Tester"], cwd=repo)
    _run_git(["config", "user.email", "tester@example.com"], cwd=repo)

    first = repo / "one.txt"
    first.write_text("one", encoding="utf-8")
    env1 = os.environ.copy()
    env1["GIT_AUTHOR_DATE"] = "2026-04-27T10:00:00+00:00"
    env1["GIT_COMMITTER_DATE"] = "2026-04-27T10:00:00+00:00"
    _run_git(["add", "one.txt"], cwd=repo)
    _run_git(["commit", "-m", "first commit"], cwd=repo, env=env1)

    second = repo / "two.txt"
    second.write_text("two", encoding="utf-8")
    env2 = os.environ.copy()
    env2["GIT_AUTHOR_DATE"] = "2026-04-28T11:00:00+00:00"
    env2["GIT_COMMITTER_DATE"] = "2026-04-28T11:00:00+00:00"
    _run_git(["add", "two.txt"], cwd=repo)
    _run_git(["commit", "-m", "second commit"], cwd=repo, env=env2)

    return repo


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_discover_finds_repo_instances(tmp_path: Path, git_repo: Path) -> None:
    source = GitLogSource(search_roots=[tmp_path], cwd=tmp_path)

    instances = source.discover()

    assert any(Path(instance.locator) == git_repo.resolve() for instance in instances)
    instance = next(instance for instance in instances if Path(instance.locator) == git_repo.resolve())
    assert instance.plugin == "git_log"
    assert instance.label.startswith("sample-repo (")
    assert instance.metadata.get("branch")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_discover_includes_cwd_repo_when_roots_missing(tmp_path: Path, git_repo: Path) -> None:
    nested = git_repo / "nested"
    nested.mkdir()

    source = GitLogSource(search_roots=[tmp_path / "missing"], cwd=nested)
    instances = source.discover()

    assert any(Path(instance.locator) == git_repo.resolve() for instance in instances)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_read_parses_git_log_events(git_repo: Path) -> None:
    source = GitLogSource(search_roots=[git_repo], cwd=git_repo)
    instance = DataSourceInstance(plugin="git_log", locator=str(git_repo), label="sample-repo")

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    event = events[0]
    assert event.source == "git_log"
    assert event.actor == "KeyPulse Tester"
    assert event.intent == "second commit"
    assert event.artifact.startswith("commit:")
    assert event.raw_ref.startswith("git:sample-repo:")
    assert event.privacy_tier == "green"
    assert event.metadata["repo_path"] == str(git_repo)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_read_includes_commit_body(tmp_path: Path) -> None:
    repo = tmp_path / "body-repo"
    repo.mkdir()
    _run_git(["init"], cwd=repo)
    _run_git(["config", "user.name", "KeyPulse Tester"], cwd=repo)
    _run_git(["config", "user.email", "tester@example.com"], cwd=repo)

    (repo / "a.txt").write_text("a", encoding="utf-8")
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = "2026-04-28T11:00:00+00:00"
    env["GIT_COMMITTER_DATE"] = "2026-04-28T11:00:00+00:00"
    _run_git(["add", "a.txt"], cwd=repo)
    body = "Why: fixes flow X.\nHow: refactor module Y.\nNotes: see issue #42."
    _run_git(["commit", "-m", "fix: subject line", "-m", body], cwd=repo, env=env)

    source = GitLogSource(search_roots=[repo], cwd=repo)
    instance = DataSourceInstance(plugin="git_log", locator=str(repo), label="body-repo")
    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert len(events) == 1
    intent = events[0].intent
    assert intent.startswith("fix: subject line\n\n")
    assert "Why: fixes flow X." in intent
    assert "Notes: see issue #42." in intent


def test_read_returns_empty_when_git_call_fails(monkeypatch) -> None:
    source = GitLogSource(search_roots=[Path("/")], cwd=Path("/"))
    instance = DataSourceInstance(plugin="git_log", locator="/tmp/no-repo", label="no-repo")

    def raise_error(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr("keypulse.sources.plugins.git_log.subprocess.run", raise_error)

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert events == []


@pytest.fixture()
def isolated_git_home(tmp_path: Path, monkeypatch) -> Path:
    # Keep results independent of the machine's global git config.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return home


def _commit_as(repo: Path, filename: str, subject: str, *, name: str, email: str, when: str) -> None:
    (repo / filename).write_text(filename, encoding="utf-8")
    env = os.environ.copy()
    env.update(
        GIT_AUTHOR_NAME=name,
        GIT_AUTHOR_EMAIL=email,
        GIT_COMMITTER_NAME=name,
        GIT_COMMITTER_EMAIL=email,
        GIT_AUTHOR_DATE=when,
        GIT_COMMITTER_DATE=when,
    )
    _run_git(["add", filename], cwd=repo)
    _run_git(["commit", "-m", subject], cwd=repo, env=env)


def _read_day(source: GitLogSource, repo: Path) -> list:
    instance = DataSourceInstance(plugin="git_log", locator=str(repo), label=repo.name)
    return list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )


def _two_author_repo(root: Path) -> Path:
    repo = root / "shared-repo"
    repo.mkdir()
    _run_git(["init"], cwd=repo)
    _run_git(["config", "user.name", "Alice"], cwd=repo)
    _run_git(["config", "user.email", "a@example.com"], cwd=repo)
    _commit_as(repo, "a1.txt", "alice work", name="Alice", email="a@example.com", when="2026-04-28T10:00:00+00:00")
    _commit_as(repo, "b1.txt", "bob work", name="Bob", email="b@example.com", when="2026-04-28T11:00:00+00:00")
    # Same display name as Alice but a different address: must not count as her.
    _commit_as(
        repo, "c1.txt", "impostor name", name="Alice", email="alice@other.example", when="2026-04-28T12:00:00+00:00"
    )
    return repo


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_read_ingests_only_commits_by_configured_user(isolated_git_home: Path, tmp_path: Path) -> None:
    repo = _two_author_repo(tmp_path)

    events = _read_day(GitLogSource(search_roots=[repo], cwd=repo), repo)

    assert [e.intent for e in events] == ["alice work"]
    assert events[0].metadata["author_email"] == "a@example.com"
    assert events[0].actor == "Alice"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_repo_without_identity_ingests_nothing(isolated_git_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "foreign-repo"
    repo.mkdir()
    _run_git(["init"], cwd=repo)
    _commit_as(repo, "x.txt", "someone else", name="Bob", email="b@example.com", when="2026-04-28T11:00:00+00:00")

    assert _read_day(GitLogSource(search_roots=[repo], cwd=repo), repo) == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_extra_author_emails_add_identities(isolated_git_home: Path, tmp_path: Path) -> None:
    repo = _two_author_repo(tmp_path)

    source = GitLogSource(search_roots=[repo], cwd=repo, author_emails=["b@example.com"])
    intents = sorted(e.intent for e in _read_day(source, repo))

    assert intents == ["alice work", "bob work"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_name_fallback_only_when_no_email_configured(isolated_git_home: Path, tmp_path: Path) -> None:
    repo = tmp_path / "name-only-repo"
    repo.mkdir()
    _run_git(["init"], cwd=repo)
    _run_git(["config", "user.name", "Alice"], cwd=repo)
    _commit_as(repo, "a1.txt", "alice work", name="Alice", email="a@example.com", when="2026-04-28T10:00:00+00:00")
    _commit_as(repo, "b1.txt", "bob work", name="Bob", email="b@example.com", when="2026-04-28T11:00:00+00:00")

    events = _read_day(GitLogSource(search_roots=[repo], cwd=repo), repo)

    assert [e.intent for e in events] == ["alice work"]
