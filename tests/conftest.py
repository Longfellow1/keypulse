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
