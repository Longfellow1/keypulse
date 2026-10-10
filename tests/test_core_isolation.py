"""V1 core is model-free: no LLM / HTTP client gets loaded, and the legacy report chain is gone."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap

# Third-party clients that would indicate a model gateway / network LLM call path.
FORBIDDEN_PREFIXES = ("anthropic", "openai", "httpx", "requests")
REMOVED_PACKAGES = (
    "keypulse.pipeline",
    "keypulse.obsidian",
    "keypulse.quality",
    "keypulse.integrations",
    "keypulse.prompts",
)


def _loaded_forbidden_modules(import_stmt: str) -> list[str]:
    code = textwrap.dedent(
        f"""
        import sys
        {import_stmt}
        bad = sorted(m for m in sys.modules if m.split(".")[0] in {FORBIDDEN_PREFIXES!r})
        print("\\n".join(bad))
        """
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def test_legacy_report_packages_are_removed() -> None:
    for name in REMOVED_PACKAGES:
        assert importlib.util.find_spec(name) is None, f"{name} should not exist in V1"


def test_daemon_entry_does_not_load_llm_or_http_clients() -> None:
    assert _loaded_forbidden_modules("import keypulse.app") == []


def test_core_modules_do_not_load_llm_or_http_clients() -> None:
    stmt = "; ".join(
        f"import {m}"
        for m in (
            "keypulse.app",
            "keypulse.cli",
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
    assert _loaded_forbidden_modules(stmt) == []
