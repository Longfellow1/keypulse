"""Evidence locator formats (pure string helpers, no DB / IO imports: safe to import from migrations).

See keypulse.store.evidence for the format table.
"""
from __future__ import annotations

import os
import re
from typing import Optional

_HEX_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
VISIT_MARKER = "#visit:"
UNRESOLVABLE_PREFIX = "unresolvable://"


def browser_locator(db_path: str, visit_id: "int | str") -> str:
    return f"{db_path}{VISIT_MARKER}{int(visit_id)}"


def split_browser_locator(locator: str) -> Optional[tuple[str, int]]:
    path, sep, tail = locator.rpartition(VISIT_MARKER)
    if sep and path and tail.isascii() and tail.isdigit() and len(tail) < 19:
        return path, int(tail)
    return None


def unresolvable_locator(source: str) -> str:
    return f"{UNRESOLVABLE_PREFIX}{source}"


def is_unresolvable_locator(locator: str) -> bool:
    return locator.startswith(UNRESOLVABLE_PREFIX)


def unresolvable_source(locator: str) -> str:
    return locator[len(UNRESOLVABLE_PREFIX):]


def split_git_locator(locator: str) -> Optional[tuple[str, str]]:
    repo, sep, commit = locator.rpartition("@")
    if sep and repo and _HEX_RE.match(commit) and os.sep in repo:
        return repo, commit
    return None
