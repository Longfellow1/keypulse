"""Cheap identity for the installed Python package."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


def _fingerprint() -> str:
    package = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        stat = path.stat()
        relative = path.relative_to(package).as_posix()
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8"))
    return digest.hexdigest()


@lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """Fingerprint this process's installed code once, for its start record."""
    return _fingerprint()


def installed_fingerprint() -> str:
    """Recompute the on-disk identity so long-running processes can detect upgrades."""
    return _fingerprint()
