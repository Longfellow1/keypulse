from __future__ import annotations

import fnmatch
from pathlib import Path


_SENSITIVE_KEYWORDS = (
    "cookies",
    "login data",
    "web data",
    "network action predictor",
    "top sites",
    "favicons",
    "keychain",
)


def is_blocked_sqlite(path: Path) -> tuple[bool, str]:
    resolved = path.expanduser().resolve(strict=False)
    path_text = str(resolved).lower()
    name = resolved.name.lower()

    for keyword in _SENSITIVE_KEYWORDS:
        if keyword in path_text:
            return True, f"sensitive:{keyword}"

    if "photos.sqlite" in name and "pictures/photos library.photoslibrary/" not in path_text:
        return True, "photos.sqlite_outside_library"

    if "chat-merged.db" in name or fnmatch.fnmatch(name, "chat-meta-summary*.db"):
        return True, "imessage_derived_db"

    if name == "history-journal" or name.endswith(".sqlite-wal") or name.endswith(".sqlite-shm"):
        return True, "sqlite_sidecar"

    return False, ""
