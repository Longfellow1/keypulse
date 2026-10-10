"""Evidence availability (PRD 3.7.2 / 3.7.3): does the original an event came from still exist?

Locators are produced by the source plugins: an absolute file path (with a 1-based line) or, for
git, "{repo}@{commit}". Only existence is checked; file content is never read back out, so a
stored (already redacted) excerpt remains the only text that can reach a Host.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Iterable, Optional

from keypulse.store.db import get_conn

GIT_TIMEOUT_SEC = 2.0
_HEX_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


def split_git_locator(locator: str) -> Optional[tuple[str, str]]:
    repo, sep, commit = locator.rpartition("@")
    if sep and repo and _HEX_RE.match(commit) and os.sep in repo:
        return repo, commit
    return None


def _git_commit_exists(repo: str, commit: str) -> bool:
    if not os.path.isdir(repo):
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "cat-file", "-e", f"{commit}^{{commit}}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SEC, check=False,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _file_has_line(path: str, line: int) -> bool:
    try:
        with open(path, "rb") as handle:
            for n, _ in enumerate(handle, start=1):
                if n >= line:
                    return True
    except OSError:
        return False
    return False


def locator_exists(locator: str, line: Optional[int] = None, *, deep: bool = True) -> bool:
    """True when the original still exists. deep=False is path/directory existence only (cheap,
    used on every search); deep=True also verifies the line (files) or the commit (git)."""
    git = split_git_locator(locator)
    if git is not None:
        repo, commit = git
        return _git_commit_exists(repo, commit) if deep else os.path.isdir(repo)
    if not os.path.isfile(locator):
        return False
    if deep and line is not None and line > 0:
        return _file_has_line(locator, line)
    return True


def refresh_availability(rows: Iterable[tuple[int, str, Optional[int], int]], *, deep: bool) -> dict[int, bool]:
    """rows: (evidence_id, locator, line, stored_available). Checks each distinct (locator, line)
    once per call and lazily writes back any change to evidence.available."""
    cache: dict[tuple[str, Optional[int]], bool] = {}
    result: dict[int, bool] = {}
    changed: list[tuple[int, int]] = []
    for evidence_id, locator, line, stored in rows:
        key = (locator, line)
        if key not in cache:
            cache[key] = locator_exists(locator, line, deep=deep)
        found = cache[key]
        if not deep and found and not (line is None and split_git_locator(locator) is None):
            # A shallow "exists" proves nothing about the line / commit: keep the stored verdict
            # (recovery of a reappeared original happens on a deep check, i.e. get_evidence).
            found = bool(stored)
        result[evidence_id] = found
        if int(found) != int(stored):
            changed.append((int(found), evidence_id))
    if changed:
        conn = get_conn()
        conn.executemany("UPDATE evidence SET available = ? WHERE id = ?", changed)
        conn.commit()
    return result
