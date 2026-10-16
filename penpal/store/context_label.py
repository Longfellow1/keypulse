"""Searchable context label: the place an imported record lives (repo, project, site, note).

Imported (semantic_event) rows never carry a window title, so their `window_title` column is free.
The label is stored there at ingest and backfilled by migration v32, which makes the FTS index find
a repo / project / site / note name. Live capture rows are NOT labelled: their window_title is real.
Pure functions over stored metadata, so ingest and the migration compute the same label.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

# Sources whose window_title is always empty today (semantic_event rows never set it).
CONTEXT_LABEL_SOURCES = frozenset({
    "git_log", "codex_cli", "claude_code", "chrome_history", "safari_history", "markdown_vault",
})
_MAX_LABEL_CHARS = 200
_MARKDOWN_PREFIX = "markdown_vault:"


def _clean(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:_MAX_LABEL_CHARS]
    return text or None


def _basename(path: Any) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return None
    stripped = path.strip().rstrip("/")
    return _clean(PurePosixPath(stripped).name) if "/" in stripped else _clean(stripped)


def _domain(url: Any) -> Optional[str]:
    """Site domain only: no scheme, userinfo, port, path or query."""
    if not isinstance(url, str) or "://" not in url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return _clean(host)


def _note_path(metadata: Mapping[str, Any]) -> Optional[str]:
    """Vault-relative note path without its extension (from `artifact`, else the raw_ref tail)."""
    rel = metadata.get("artifact")
    if not isinstance(rel, str) or not rel:
        raw_ref = metadata.get("raw_ref")
        vault = metadata.get("vault_name")
        prefix = f"{_MARKDOWN_PREFIX}{vault}:" if isinstance(vault, str) else None
        if isinstance(raw_ref, str) and prefix and raw_ref.startswith(prefix):
            rel = raw_ref[len(prefix):]
    if not isinstance(rel, str) or not rel.strip() or rel.startswith("/"):
        return None
    return _clean(PurePosixPath(rel).with_suffix("").as_posix())


def _claude_project(project_dir: Any) -> Optional[str]:
    """Claude Code keeps transcripts under ~/.claude/projects/<cwd with "/" replaced by "-">. Drop the
    encoded home prefix ("-Users-<name>-") so the label is the project part ("Go-CorpusFlow"), not the
    user's account path; the project name stays searchable as a substring."""
    name = _basename(project_dir)
    if not name:
        return None
    parts = name.lstrip("-").split("-")
    if len(parts) > 2 and parts[0] in ("Users", "home"):
        rest = "-".join(parts[2:])
        return _clean(rest) or name
    return name


def context_label(source: str, metadata: Mapping[str, Any]) -> Optional[str]:
    """The place label for one stored record, or None when the source has none."""
    if source == "git_log":
        return _clean(metadata.get("repo_name"))
    if source == "codex_cli":
        return _clean(metadata.get("project")) or _basename(metadata.get("project_dir") or metadata.get("cwd"))
    if source == "claude_code":
        return _claude_project(metadata.get("project_dir"))
    if source in ("chrome_history", "safari_history"):
        return _domain(metadata.get("artifact"))
    if source == "markdown_vault":
        return _note_path(metadata)
    return None
