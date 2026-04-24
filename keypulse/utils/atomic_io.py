"""
Atomic file-write helper: write to a temp file then os.replace into place.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path


def atomic_write_text(
    path: "Path | str",
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Write *content* to *path* atomically using a temp file + os.replace.

    The temporary file is created in the same directory as *path* so that
    os.replace is a same-filesystem rename (i.e. truly atomic on POSIX).
    The temp file is always cleaned up on failure.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_name = f".tmp.{target.name}.{os.getpid()}.{uuid.uuid4().hex}"
    tmp_path = target.parent / tmp_name

    try:
        with tmp_path.open("w", encoding=encoding) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
