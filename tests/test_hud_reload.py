from __future__ import annotations

from penpal.foundation.runtime_identity import RuntimeIdentity
from penpal.hud.reload import HUDReloader


def test_hud_identity_change_execs_original_command_with_injected_exec():
    started = RuntimeIdentity("code-a", "1.0", "config-a", 34, 34)
    changed = RuntimeIdentity("code-b", "1.0", "config-a", 34, 34)
    current = [started]
    clock = [0.0]
    calls = []
    reloader = HUDReloader(
        started,
        lambda: current[0],
        execv=lambda executable, argv: calls.append((executable, list(argv))),
        executable="/stable/penpal",
        argv=["/stable/penpal", "hud"],
        interval_sec=2,
        clock=lambda: clock[0],
    )

    clock[0] = 2
    current[0] = changed
    assert reloader.check() is True
    reloader.exec()

    assert calls == [("/stable/penpal", ["/stable/penpal", "hud"])]
