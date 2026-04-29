from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_quiet(path: Path | None = None) -> int:
    """Load KEY=VALUE lines from ~/.keypulse/.env into os.environ.

    - Existing env vars are NOT overwritten (env wins over file).
    - Lines starting with `#` and blank lines are skipped.
    - Surrounding single/double quotes on the value are stripped.
    - Any error is silently swallowed (file may be missing or unreadable).

    Returns the number of keys loaded.
    """
    target = path or (Path.home() / ".keypulse" / ".env")
    if not target.exists():
        return 0
    loaded = 0
    try:
        for raw in target.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key or not _is_valid_env_key(key):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key not in os.environ:
                os.environ[key] = value
                loaded += 1
    except Exception:
        return loaded
    return loaded


def _is_valid_env_key(key: str) -> bool:
    if not key:
        return False
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in key)
