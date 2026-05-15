from __future__ import annotations

from keypulse.capture.base import reconcile_orphan_lock


def test_reconcile_orphan_lock_happy_path_terminates_and_cleans(monkeypatch, tmp_path):
    lock_path = tmp_path / "watcher.lock"
    lock_path.write_text("4321", encoding="utf-8")
    calls: list[tuple[int, int]] = []
    alive_checks = {"count": 0}

    def fake_kill(pid: int, sig: int):
        calls.append((pid, sig))
        if sig == 0:
            alive_checks["count"] += 1
            if alive_checks["count"] >= 2:
                raise ProcessLookupError()

    monkeypatch.setattr("keypulse.capture.base.os.kill", fake_kill)

    assert reconcile_orphan_lock(lock_path, current_pid=9999) is True
    assert not lock_path.exists()
    assert (4321, 15) in calls


def test_reconcile_orphan_lock_removes_stale_pid(monkeypatch, tmp_path):
    lock_path = tmp_path / "watcher.pid"
    lock_path.write_text("9998", encoding="utf-8")

    def fake_kill(pid: int, sig: int):
        raise ProcessLookupError()

    monkeypatch.setattr("keypulse.capture.base.os.kill", fake_kill)

    assert reconcile_orphan_lock(lock_path, current_pid=7777) is True
    assert not lock_path.exists()
