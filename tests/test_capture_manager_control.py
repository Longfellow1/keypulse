from __future__ import annotations

from penpal.capture.manager import CaptureManager
from penpal.config import Config


def test_manager_sync_control_state_pauses_and_resumes(monkeypatch):
    manager = CaptureManager(Config())
    calls: list[str] = []

    monkeypatch.setattr("penpal.capture.manager.get_state", lambda key: "paused" if key == "status" else "")
    monkeypatch.setattr(manager, "pause", lambda: calls.append("pause"))
    monkeypatch.setattr(manager, "resume", lambda: calls.append("resume"))
    manager._sync_control_state()

    assert calls == ["pause"]
