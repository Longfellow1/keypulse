"""Process replacement helpers that preserve the command-line invocation."""
from __future__ import annotations

import os
import sys
from typing import Callable, Sequence


def current_exec_command() -> tuple[str, list[str]]:
    """Return the interpreter invocation that started this process."""
    original = list(getattr(sys, "orig_argv", ()))
    if original and original[0]:
        # venv Python usually reports its base interpreter in orig_argv[0]. Re-enter through
        # sys.executable so the virtual environment's site-packages stay active across exec.
        return sys.executable, [sys.executable, *original[1:]]
    executable = sys.executable
    return executable, [executable, *sys.argv]


def exec_self(
    *,
    execv: Callable[[str, Sequence[str]], object] = os.execv,
    executable: str | None = None,
    argv: Sequence[str] | None = None,
) -> None:
    default_executable, default_argv = current_exec_command()
    target = executable or default_executable
    arguments = list(argv) if argv is not None else default_argv
    execv(target, arguments)
