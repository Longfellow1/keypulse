"""Core daemon startup must not load legacy report / Obsidian / quality modules."""
from __future__ import annotations

import subprocess
import sys
import textwrap

LEGACY_PREFIXES = ("keypulse.pipeline", "keypulse.obsidian", "keypulse.quality")


def _loaded_legacy_modules(import_stmt: str) -> list[str]:
    code = textwrap.dedent(
        f"""
        import sys
        {import_stmt}
        legacy = sorted(m for m in sys.modules if m.startswith({LEGACY_PREFIXES!r}))
        print("\\n".join(legacy))
        """
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def test_daemon_entry_does_not_load_legacy_modules() -> None:
    assert _loaded_legacy_modules("import keypulse.app") == []


def test_core_modules_do_not_load_legacy_modules() -> None:
    stmt = "; ".join(
        f"import {m}"
        for m in (
            "keypulse.app",
            "keypulse.sources",
            "keypulse.sources.registry",
            "keypulse.sources.scheduler",
            "keypulse.hud.summary",
            "keypulse.health.self_heal",
            "keypulse.health.check",
            "keypulse.services.export",
            "keypulse.search.engine",
        )
    )
    assert _loaded_legacy_modules(stmt) == []
