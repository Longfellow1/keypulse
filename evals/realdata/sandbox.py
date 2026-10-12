"""Generate the macOS sandbox-exec profile that isolates the bash arm."""
from __future__ import annotations

from pathlib import Path


def _lit(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_profile(deny_paths: list[Path | str], deny_regexes: list[str] | None = None) -> str:
    """Allow everything except reading/writing the given subpaths and regex-matched paths.

    Prompt-level deny rules alone were bypassed in the M1 first run; this is OS-level.
    Regexes are SBPL regexes (e.g. a session id); a double quote cannot be expressed and is rejected.
    """
    lines = ["(version 1)", "(allow default)", "(deny file-read* file-write*"]
    for rx in deny_regexes or []:
        if not rx or '"' in rx or "\n" in rx:
            raise ValueError("deny regex must be non-empty and contain no double quote or newline")
        lines.append(f'  (regex #"{rx}")')
    for p in deny_paths:
        p = str(Path(p).expanduser())
        if not p or p == "/":
            raise ValueError("refusing to deny the filesystem root")
        lines.append(f'  (subpath "{_lit(p)}")')
    if len(lines) == 3:
        raise ValueError("profile would deny nothing")
    lines.append(")")
    return "\n".join(lines) + "\n"
