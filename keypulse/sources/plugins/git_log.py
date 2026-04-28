from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent
from keypulse.utils.logging import get_logger


LOGGER = get_logger("sources.git_log")


class GitLogSource(DataSource):
    name = "git_log"
    privacy_tier = "green"
    liveness = "always"
    description = "Git commit history scanner"

    def __init__(self, *, search_roots: Iterable[Path] | None = None, cwd: Path | None = None) -> None:
        self._cwd = (cwd or Path.cwd()).resolve()
        self._search_roots = list(search_roots) if search_roots is not None else self._default_roots(self._cwd)

    def discover(self) -> list[DataSourceInstance]:
        if shutil.which("git") is None:
            return []

        repos: set[Path] = set()
        cwd_repo = self._repo_root_for(self._cwd)
        if cwd_repo is not None:
            repos.add(cwd_repo)

        for root in self._search_roots:
            root_path = Path(root).expanduser()
            if not root_path.exists():
                continue
            for repo in self._find_repos(root_path):
                repos.add(repo)

        instances: list[DataSourceInstance] = []
        for repo in sorted(repos):
            branch = self._branch_name(repo) or "unknown"
            last_commit_date = self._last_commit_date(repo)
            metadata = {
                "branch": branch,
                "last_commit_date": last_commit_date,
                "repo_name": repo.name,
            }
            instances.append(
                DataSourceInstance(
                    plugin=self.name,
                    locator=str(repo),
                    label=f"{repo.name} ({branch})",
                    metadata=metadata,
                )
            )
        return instances

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        if shutil.which("git") is None:
            return iter(())

        since_utc = since.astimezone(timezone.utc)
        until_utc = until.astimezone(timezone.utc)
        cmd = [
            "git",
            "-C",
            instance.locator,
            "log",
            f"--since={since_utc.isoformat()}",
            f"--until={until_utc.isoformat()}",
            "--pretty=format:%H%x09%aI%x09%an%x09%s",
            "--no-merges",
        ]

        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("git_log read failed for %s: %s", instance.locator, exc)
            return iter(())

        if result.returncode != 0:
            LOGGER.warning(
                "git_log read failed for %s (code=%s): %s",
                instance.locator,
                result.returncode,
                (result.stderr or "").strip(),
            )
            return iter(())

        return self._parse_git_log(instance, result.stdout)

    def _parse_git_log(self, instance: DataSourceInstance, raw: str) -> Iterator[SemanticEvent]:
        repo_name = Path(instance.locator).name
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            commit_hash, author_time, author_name, subject = parts
            try:
                event_time = datetime.fromisoformat(author_time).astimezone(timezone.utc)
            except ValueError:
                continue
            yield SemanticEvent(
                time=event_time,
                source=self.name,
                actor=author_name,
                intent=subject,
                artifact=f"commit:{commit_hash[:7]}",
                raw_ref=f"git:{repo_name}:{commit_hash}",
                privacy_tier=self.privacy_tier,
                metadata={
                    "repo_path": instance.locator,
                    "full_hash": commit_hash,
                },
            )

    def _default_roots(self, cwd: Path) -> list[Path]:
        home = Path.home()
        roots = [
            home / "Go",
            home / "Code",
            home / "code",
            home / "Projects",
            home / "projects",
        ]
        roots.append(cwd)
        return roots

    def _find_repos(self, root: Path) -> list[Path]:
        cmd = [
            "find",
            str(root),
            "-maxdepth",
            "4",
            "-type",
            "d",
            "-name",
            ".git",
        ]
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except Exception:
            return []
        if result.returncode != 0:
            return []

        repos: list[Path] = []
        for line in result.stdout.splitlines():
            git_dir = line.strip()
            if not git_dir:
                continue
            repo_path = Path(git_dir).resolve().parent
            repos.append(repo_path)
        return repos

    def _repo_root_for(self, path: Path) -> Path | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        top = (result.stdout or "").strip()
        if not top:
            return None
        return Path(top).resolve()

    def _branch_name(self, repo: Path) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        branch = (result.stdout or "").strip()
        return branch or None

    def _last_commit_date(self, repo: Path) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "log", "-1", "--date=short", "--pretty=format:%ad"],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        value = (result.stdout or "").strip()
        return value or None
