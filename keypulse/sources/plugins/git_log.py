from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from keypulse.sources.types import MAX_INTENT_CHARS, DataSource, DataSourceInstance, SemanticEvent
from keypulse.utils.logging import get_logger


LOGGER = get_logger("sources.git_log")

_OVERLAP_SEC = 2 * 86400
_FIRST_SYNC_MAX_COMMITS = 20000


class GitLogSource(DataSource):
    name = "git_log"
    privacy_tier = "green"
    liveness = "always"
    description = "Git commit history scanner"

    def __init__(
        self,
        *,
        search_roots: Iterable[Path] | None = None,
        cwd: Path | None = None,
        use_cursor: bool = False,
        author_emails: Iterable[str] | None = None,
    ) -> None:
        # use_cursor=True: per-repo incremental sync driven by a committer-time
        # high-water mark in `sync_cursors`; the since/until window is ignored.
        self._use_cursor = use_cursor
        # Extra identities besides each repo's own git user.email (e.g. a second address).
        self._extra_emails = tuple(e.strip() for e in (author_emails or ()) if e and e.strip())
        self._pending_cursors: dict[int, dict[str, Any]] = {}
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

        # Fail closed: only the user's own commits. No identity in this repo means nothing is ingested.
        emails, name = self._author_identity(instance.locator)
        if not emails and name is None:
            LOGGER.debug("git_log skip %s: no user identity configured; ingesting nothing", instance.locator)
            return iter(())
        if emails:
            author_args = ["-F", *(f"--author=<{email}>" for email in emails)]
        else:
            author_args = ["-F", f"--author={name} <"]

        source_id = None
        prior_ts: int | None = None
        if self._use_cursor:
            from keypulse.store.sync_repo import ensure_source, get_cursor

            source_id = ensure_source(self.name, instance.locator)
            last = get_cursor(source_id).get("last_commit_ts")
            prior_ts = last if isinstance(last, int) else None
            if prior_ts is not None:
                # overlap so same-second / skewed commits are not missed; external_id dedups
                range_args = [f"--since=@{max(prior_ts - _OVERLAP_SEC, 0)}"]
            else:
                range_args = [f"--max-count={_FIRST_SYNC_MAX_COMMITS}"]
        else:
            since_utc = since.astimezone(timezone.utc)
            until_utc = until.astimezone(timezone.utc)
            range_args = [f"--since={since_utc.isoformat()}", f"--until={until_utc.isoformat()}"]
        cmd = [
            "git",
            "-C",
            instance.locator,
            "log",
            *range_args,
            *author_args,
            "--pretty=format:%H%x09%cI%x09%an%x09%ae%x09%s%x09%b%x00",
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

        # git already filtered by --author; this exact check guards against loose substring matches.
        own_emails = {e.lower() for e in emails}
        events = []
        for event in self._parse_git_log(instance, result.stdout):
            if emails:
                is_own = str(event.metadata.get("author_email", "")).lower() in own_emails
            else:
                is_own = event.actor == name
            if is_own:
                events.append(event)
        if source_id is not None:
            # `recent` = hashes inside the overlap window, so the overlap re-scan yields only new commits.
            recent: dict[str, int] = dict(get_cursor(source_id).get("recent") or {})
            events = [e for e in events if e.external_id not in recent]
            for e in events:
                recent[str(e.external_id)] = int(e.time.timestamp())
            high = max([prior_ts or 0, *recent.values()])
            recent = {h: t for h, t in recent.items() if t >= high - _OVERLAP_SEC}
            if high:
                self._pending_cursors[source_id] = {"last_commit_ts": high, "recent": recent}
        return iter(events)

    def commit_cursors(self, *, failed: bool = False) -> None:
        """Persist each repo's high-water mark once the registry has stored the events."""
        from keypulse.store.sync_repo import record_sync_error, save_cursor

        pending, self._pending_cursors = self._pending_cursors, {}
        for source_id, cursor in pending.items():
            if failed:
                record_sync_error(source_id, "persist failed; cursor not advanced")
            else:
                save_cursor(source_id, cursor)

    def _parse_git_log(self, instance: DataSourceInstance, raw: str) -> Iterator[SemanticEvent]:
        repo_name = Path(instance.locator).name
        # NUL-separated commits because %b body contains real newlines that
        # would break splitlines(). Tab-separated 5 fields per commit:
        # hash, author_time, author_name, subject, body.
        for chunk in raw.split("\x00"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = chunk.split("\t", 5)
            if len(parts) < 5:
                continue
            commit_hash = parts[0]
            author_time = parts[1]
            author_name = parts[2]
            author_email = parts[3]
            subject = parts[4]
            body = parts[5].strip() if len(parts) == 6 else ""
            try:
                event_time = datetime.fromisoformat(author_time).astimezone(timezone.utc)
            except ValueError:
                continue
            intent = (f"{subject}\n\n{body}" if body else subject)[:MAX_INTENT_CHARS]
            yield SemanticEvent(
                time=event_time,
                source=self.name,
                actor=author_name,
                intent=intent,
                artifact=f"commit:{commit_hash[:7]}",
                raw_ref=f"git:{repo_name}:{commit_hash}",
                privacy_tier=self.privacy_tier,
                metadata={
                    "repo_path": instance.locator,
                    "full_hash": commit_hash,
                    "repo_name": repo_name,
                    "author_email": author_email,
                },
                external_id=commit_hash,
                source_locator=instance.locator,
                evidence_locator=f"{instance.locator}@{commit_hash}",
                kind="commit",
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

    def _author_identity(self, repo: str) -> tuple[list[str], str | None]:
        """Return (emails, name) identifying the user's commits in `repo`.

        Emails come from the repo's git config (local, then global) plus any extras.
        The name is used only when no email is known. ([], None) means no identity.
        """
        emails: list[str] = []
        own = self._git_config(repo, "user.email")
        for candidate in [own, *self._extra_emails]:
            if candidate and candidate not in emails:
                emails.append(candidate)
        if emails:
            return emails, None
        return [], self._git_config(repo, "user.name")

    def _git_config(self, repo: str, key: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", repo, "config", "--get", key],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip() or None

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
