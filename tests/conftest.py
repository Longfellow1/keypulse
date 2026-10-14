from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
    from keypulse.health import capture_health

    capture_health.reset_cache()
    monkeypatch.setattr("keypulse.mcp.server.current_capture_health", lambda: None)
    yield
    capture_health.reset_cache()
