from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_OPT_IN_MARKERS = {
    "slow": ("PENPAL_RUN_SLOW", "opt-in performance benchmark; set PENPAL_RUN_SLOW=1"),
    "system_smoke": (
        "PENPAL_RUN_SYSTEM_SMOKE",
        "opt-in system smoke; set PENPAL_RUN_SYSTEM_SMOKE=1",
    ),
}


def pytest_collection_modifyitems(items):
    for item in items:
        for marker, (env_var, reason) in _OPT_IN_MARKERS.items():
            if marker in item.keywords and os.environ.get(env_var) != "1":
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(scope="session", autouse=True)
def _no_object_object_artifacts() -> None:
    for path in ROOT.glob("<object object*"):
        path.unlink(missing_ok=True)
    yield
    leaked = sorted(path.name for path in ROOT.glob("<object object*"))
    assert leaked == [], f"unexpected root artifacts: {', '.join(leaked)}"


@pytest.fixture(autouse=True)
def _mcp_capture_health_off_by_default(monkeypatch):
    """MCP tests assert exact payloads; a fresh test DB has no daemon heartbeat, which would add a
    capture_health warning. tests/test_capture_health.py exercises the real thing explicitly."""
    from penpal.health import capture_health

    capture_health.reset_cache()
    monkeypatch.setattr("penpal.mcp.server.current_capture_health", lambda: None)
    yield
    capture_health.reset_cache()


@pytest.fixture(autouse=True)
def _no_host_legacy_process_scan(monkeypatch):
    """Data-dir migration refuses to run while old `keypulse mcp serve` processes are alive. That scan
    looks at the whole machine, so on a developer box with KeyPulse running it would block every
    migration test. tests/test_data_dir_migration.py exercises the real scan explicitly."""
    from penpal.utils import paths

    monkeypatch.setattr(paths, "_find_legacy_mcp_serve_processes", lambda: [])
