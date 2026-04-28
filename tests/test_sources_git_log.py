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
