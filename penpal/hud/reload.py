"""AppKit-free HUD identity checks and in-place process replacement."""
from __future__ import annotations

import os
import time
from typing import Callable, Sequence

from penpal.foundation.runtime_identity import IdentityWatcher, RuntimeIdentity
from penpal.runtime.process import current_exec_command, exec_self


class HUDReloader:
    def __init__(
        self,
        started: RuntimeIdentity,
        reader: Callable[[], RuntimeIdentity],
        *,
        execv: Callable[[str, Sequence[str]], object] = os.execv,
        executable: str | None = None,
        argv: Sequence[str] | None = None,
        interval_sec: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.watcher = IdentityWatcher(started, reader, interval_sec=interval_sec, clock=clock)
        default_executable, default_argv = current_exec_command()
        self.executable = executable or default_executable
        self.argv = list(argv) if argv is not None else default_argv
        self.execv = execv

    def check(self) -> bool:
        return self.watcher.check()

    def exec(self) -> None:
        exec_self(execv=self.execv, executable=self.executable, argv=self.argv)
