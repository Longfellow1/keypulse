from __future__ import annotations

import time
from datetime import datetime, timezone

from keypulse.health import self_heal
from keypulse.store.db import init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


def _ok_step(name: str):
    return self_heal.StepResult(name=name, ok=True, message="ok")


def test_self_heal_happy_path(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    monkeypatch.setattr(self_heal, "step_reconcile_locks", lambda **kwargs: _ok_step("step1_locks"))
    monkeypatch.setattr(self_heal, "step_refresh_capabilities", lambda **kwargs: _ok_step("step2_capabilities"))
    monkeypatch.setattr(self_heal, "step_restart_daemon", lambda **kwargs: _ok_step("step3_restart"))
    monkeypatch.setattr(self_heal, "step_wait_daemon_ready", lambda **kwargs: _ok_step("step4_ready"))
    monkeypatch.setattr(self_heal, "step_wait_core_emit", lambda **kwargs: _ok_step("step5_core_emit"))
    monkeypatch.setattr(self_heal, "step_rerun_daily_if_needed", lambda **kwargs: _ok_step("step6_daily"))

    result = self_heal.run_self_heal(dry_run=True, source="test")

    assert result["status"] == "ok"
    assert [item["name"] for item in result["steps"]] == [
        "step1_locks",
        "step2_capabilities",
        "step3_restart",
        "step4_ready",
        "step5_core_emit",
        "step6_daily",
    ]


def test_self_heal_aborts_on_step4_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    monkeypatch.setattr(self_heal, "step_reconcile_locks", lambda **kwargs: _ok_step("step1_locks"))
    monkeypatch.setattr(self_heal, "step_refresh_capabilities", lambda **kwargs: _ok_step("step2_capabilities"))
    monkeypatch.setattr(self_heal, "step_restart_daemon", lambda **kwargs: _ok_step("step3_restart"))
    monkeypatch.setattr(
        self_heal,
        "step_wait_daemon_ready",
        lambda **kwargs: self_heal.StepResult(
            name="step4_ready",
            ok=False,
            message="30 秒内 daemon 未就绪",
            suggested_action="检查权限",
        ),
    )

    result = self_heal.run_self_heal(dry_run=True, source="test")

    assert result["status"] == "failed"
    assert result["failed_step"] == "step4_ready"


def test_self_heal_step5_core_not_recovered(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    monkeypatch.setattr(self_heal, "step_reconcile_locks", lambda **kwargs: _ok_step("step1_locks"))
    monkeypatch.setattr(self_heal, "step_refresh_capabilities", lambda **kwargs: _ok_step("step2_capabilities"))
    monkeypatch.setattr(self_heal, "step_restart_daemon", lambda **kwargs: _ok_step("step3_restart"))
    monkeypatch.setattr(self_heal, "step_wait_daemon_ready", lambda **kwargs: _ok_step("step4_ready"))
    monkeypatch.setattr(
        self_heal,
        "step_wait_core_emit",
        lambda **kwargs: self_heal.StepResult(
            name="step5_core_emit",
            ok=False,
            message="核心数据源仍未恢复",
            suggested_action="检查系统设置权限",
        ),
    )

    result = self_heal.run_self_heal(dry_run=True, source="test")

    assert result["status"] == "failed"
    assert result["failed_step"] == "step5_core_emit"


def test_kickstart_ts_uses_utc_so_core_emit_count_finds_new_rows(monkeypatch, tmp_path):
    """退化锁：kickstart_ts 必须用 UTC，否则跟 raw_events.ts_start 字符串比较错位
    导致 step_wait_core_emit 永远 0 → self_heal 反复 SIGTERM daemon。
    见 docs/incident-2026-05-17-selfheal-loop.md 复发。"""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    init_db(tmp_path / ".keypulse" / "keypulse.db")

    kickstart_ts = datetime.now(timezone.utc).isoformat()
    time.sleep(0.01)
    insert_raw_event(RawEvent(
        source="keyboard_chunk",
        event_type="text_chunk",
        ts_start=datetime.now(timezone.utc).isoformat(),
        content_text="hi",
    ))

    assert self_heal._core_emit_count_since(kickstart_ts) >= 1
