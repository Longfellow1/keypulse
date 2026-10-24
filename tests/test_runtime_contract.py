from __future__ import annotations

import itertools
import importlib
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from penpal import __version__
from penpal.runtime import RuntimeState, RuntimeStatus, evaluate, state_text
from penpal.runtime.record import Component, Progress, RuntimeRecord, read_record, write_record

EVALUATOR = importlib.import_module("penpal.runtime.evaluate")

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
START = "Mon Oct 19 08:14:02 2026"
PID = 34561
FINGERPRINT = "installed-code"


def make_record(*, last_done: datetime = NOW, fingerprint: str = FINGERPRINT,
                started_at: datetime = NOW - timedelta(minutes=5), pid: int = PID) -> RuntimeRecord:
    return RuntimeRecord(
        generation="generation-1",
        pid=pid,
        process_start_time=START,
        version=__version__,
        code_fingerprint=fingerprint,
        started_at=started_at.isoformat(),
        written_at=NOW.isoformat(),
        progress={"core_loop": Progress(17, last_done.isoformat())},
        components={"storage": Component("running"), "window": Component("idle")},
    )


@pytest.fixture(autouse=True)
def temporary_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PENPAL_RUNTIME_DISABLE_LAUNCHD_PROBE", raising=False)
    monkeypatch.chdir(tmp_path)
    return home


@pytest.mark.parametrize(
    "record_kind,launchd_state,progress,fingerprint,paused",
    list(itertools.product(
        ("present", "absent", "corrupt"),
        ("loaded", "unloaded", "pid_mismatch"),
        ("fresh", "stale"),
        ("same", "different"),
        (False, True),
    )),
)
def test_r1_state_matrix(monkeypatch, record_kind, launchd_state, progress, fingerprint, paused, tmp_path):
    record = None
    if record_kind == "present":
        record = make_record(
            last_done=NOW if progress == "fresh" else NOW - timedelta(seconds=31),
            fingerprint=FINGERPRINT if fingerprint == "same" else "different-code",
        )
    loaded = launchd_state != "unloaded"
    launchd_pid = PID + 1 if launchd_state == "pid_mismatch" else PID if loaded else None
    monkeypatch.setattr(EVALUATOR, "is_installed", lambda: True)
    monkeypatch.setattr(EVALUATOR, "process_start_time", lambda _pid: START)

    result = evaluate(
        now=NOW,
        record_reader=lambda: record,
        launchd_probe=lambda: {"loaded": loaded, "pid": launchd_pid, "last_exit": None},
        installed_fingerprint=lambda: FINGERPRINT,
        paused_reader=lambda: paused,
    )

    if record is None:
        expected = RuntimeState.STARTING if loaded else RuntimeState.NOT_RUNNING
    elif launchd_state == "pid_mismatch":
        expected = RuntimeState.NOT_RUNNING
    elif fingerprint == "different":
        expected = RuntimeState.OUTDATED
    elif paused:
        expected = RuntimeState.PAUSED
    elif progress == "stale":
        expected = RuntimeState.HUNG
    else:
        expected = RuntimeState.RUNNING
    assert result.state is expected
    assert result.code == expected.value
    _assert_consumers_expose_same_runtime_state(result, monkeypatch, tmp_path)


def test_record_read_write_is_atomic_and_corruption_is_absent(tmp_path):
    path = tmp_path / "home" / ".penpal" / "run" / "daemon.json"
    record = make_record()
    write_record(record, path)
    assert read_record(path) == record
    assert not path.with_name(f".{path.name}.{os.getpid()}.tmp").exists()
    path.write_text("not-json", encoding="utf-8")
    assert read_record(path) is None
    assert read_record(path.with_name("missing.json")) is None


def test_pid_reuse_and_starting_grace(monkeypatch):
    record = make_record(last_done=NOW - timedelta(seconds=31), started_at=NOW - timedelta(seconds=4))
    monkeypatch.setattr(EVALUATOR, "is_installed", lambda: True)
    monkeypatch.setattr(EVALUATOR, "process_start_time", lambda _pid: "different-process")
    args = dict(
        now=NOW,
        record_reader=lambda: record,
        launchd_probe=lambda: {"loaded": False, "pid": None, "last_exit": None},
        installed_fingerprint=lambda: FINGERPRINT,
        paused_reader=lambda: False,
    )
    assert evaluate(**args).state is RuntimeState.NOT_RUNNING

    monkeypatch.setattr(EVALUATOR, "process_start_time", lambda _pid: START)
    assert evaluate(**args).state is RuntimeState.STARTING
    args["now"] = NOW + timedelta(seconds=31)
    assert evaluate(**args).state is RuntimeState.HUNG


def test_quiet_but_healthy_progress_is_running_for_thirty_simulated_minutes(monkeypatch):
    record = make_record()
    monkeypatch.setattr(EVALUATOR, "is_installed", lambda: True)
    monkeypatch.setattr(EVALUATOR, "process_start_time", lambda _pid: START)
    statuses = []
    for minute in range(31):
        now = NOW + timedelta(minutes=minute)
        record = RuntimeRecord(
            **(record.__dict__ | {
                "written_at": now.isoformat(),
                "progress": {"core_loop": Progress(minute + 17, now.isoformat())},
            })
        )
        statuses.append(evaluate(
            now=now,
            record_reader=lambda record=record: record,
            launchd_probe=lambda: {"loaded": False, "pid": None, "last_exit": None},
            installed_fingerprint=lambda: FINGERPRINT,
            paused_reader=lambda: False,
        ).state)
    assert statuses == [RuntimeState.RUNNING] * 31


def test_launchd_probe_parses_output_with_injected_runner(monkeypatch):
    from penpal.runtime import launchd

    monkeypatch.delenv("PENPAL_RUNTIME_DISABLE_LAUNCHD_PROBE", raising=False)
    monkeypatch.setattr(launchd.os, "uname", lambda: SimpleNamespace(sysname="Darwin"))
    monkeypatch.setattr(launchd.os, "getuid", lambda: 501)
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="state = running\npid = 34561\nlast exit code = 0", stderr="")

    assert launchd.probe_daemon(runner=runner) == {"loaded": True, "pid": 34561, "last_exit": 0}
    assert calls == [["launchctl", "print", "gui/501/com.penpal.daemon"]]


def _assert_consumers_expose_same_runtime_state(runtime, monkeypatch, tmp_path):
    from penpal import cli
    from penpal.health import capture_health
    from penpal.health import check as healthcheck
    from penpal.health.product_delivery import DeliveryHealth
    from penpal.config import Config
    from penpal.hud.snapshot import Snapshot
    from penpal.hud.viewmodel import status_bar
    from penpal.store.db import close, init_db
    from click.testing import CliRunner

    monkeypatch.setattr(cli, "evaluate", lambda: runtime)
    monkeypatch.setattr(capture_health, "evaluate", lambda **_kwargs: runtime)
    monkeypatch.setattr("penpal.hud.snapshot.evaluate", lambda **_kwargs: runtime)
    monkeypatch.setattr(healthcheck, "evaluate", lambda **_kwargs: runtime)
    monkeypatch.setattr(healthcheck, "_load_config", lambda _path: (Config(), True))
    monkeypatch.setattr(healthcheck, "evaluate_delivery_health", lambda **_kwargs: DeliveryHealth([], {}, False))
    monkeypatch.setattr(healthcheck, "_capabilities_snapshot", lambda: ({}, ""))
    monkeypatch.setattr(healthcheck, "write_health_report", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(healthcheck, "HEALTH_JSON_PATH", tmp_path / "health.json")
    monkeypatch.setattr(cli, "require_db", lambda _cfg: None)
    monkeypatch.setattr(cli, "get_config", Config)
    monkeypatch.setattr(cli, "get_state", lambda key: {"status": "running", "started_at": "—", "last_flush": "—"}.get(key))
    monkeypatch.setattr(cli, "disabled_sources", lambda: frozenset())
    monkeypatch.setattr("penpal.sources.importer.progress_rows", lambda: [])
    monkeypatch.setattr("penpal.sources.history_gate.historical_imports_allowed", lambda: True)
    monkeypatch.setattr(cli, "keyboard_capture_enabled", lambda _enabled: False)
    init_db(Config().db_path_expanded)

    status_result = CliRunner().invoke(cli.main, ["status", "--plain"])
    assert status_result.exit_code == 0, status_result.output
    assert f"runtime_state={runtime.code}" in status_result.output

    config_path = tmp_path / "config.toml"
    config_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "get_config_path", lambda: config_path)
    monkeypatch.setattr(cli, "_stale_moved_dev_venv_script", lambda: None)
    monkeypatch.setattr(cli, "_legacy_mcp_config_findings", lambda: [])
    monkeypatch.setattr(cli, "_launchd_label_loaded", lambda _label: False)
    monkeypatch.setattr(cli, "_launchd_plist_path", lambda label: tmp_path / f"{label}.plist")
    monkeypatch.setattr(capture_health, "permission_report", lambda *_args, **_kwargs: [])
    from penpal.sources import onboarding, registry
    monkeypatch.setattr(onboarding, "source_problems", lambda _names: [])
    monkeypatch.setattr(registry, "list_sources", lambda: [])
    doctor_result = CliRunner().invoke(cli.main, ["doctor", "--plain"])
    assert doctor_result.output.splitlines(), repr(doctor_result.exception)
    assert doctor_result.output.splitlines()[0].startswith("后台服务:")
    assert f"state={runtime.code}" in doctor_result.output.splitlines()[0]

    executable = tmp_path / "bin" / "penpal"
    executable.parent.mkdir(exist_ok=True)
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "_keypulse_executable_path", lambda: executable)
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: Config()))
    install_result = CliRunner().invoke(cli.main, ["install", "doctor", "--plain"])
    assert f"runtime={'OK' if not runtime.failed else 'FAIL'} state={runtime.code}" in install_result.output

    line = cli._runtime_status_line(cli.evaluate())
    assert runtime.code in line
    bar = status_bar(Snapshot(now=NOW, runtime_status=runtime))
    assert bar["code"] == runtime.code
    from penpal.runtime import RuntimeState
    if runtime.state is RuntimeState.RUNNING:
        assert capture_health.compute_capture_health() is None
    else:
        assert capture_health.compute_capture_health()["code"] == runtime.code

    health_result = healthcheck.run_healthcheck()
    assert health_result["daemon"]["state"] == runtime.code
    close()


def test_every_consumer_exposes_each_runtime_state(monkeypatch, temporary_home, tmp_path):
    states = [RuntimeState.NOT_INSTALLED, RuntimeState.NOT_RUNNING, RuntimeState.STARTING,
              RuntimeState.RUNNING, RuntimeState.HUNG, RuntimeState.OUTDATED, RuntimeState.PAUSED]
    for state in states:
        issues = () if state in {RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.PAUSED} else (
            EVALUATOR._state_issue(state),
        )
        runtime = RuntimeStatus(
            state, issues=tuple(issue for issue in issues if issue is not None),
            pid=PID if state not in {RuntimeState.NOT_INSTALLED, RuntimeState.NOT_RUNNING} else None,
        )
        _assert_consumers_expose_same_runtime_state(runtime, monkeypatch, tmp_path)


@pytest.mark.slow
@pytest.mark.skipif(
    sys.platform != "darwin" or os.environ.get("PENPAL_RUN_SLOW") != "1",
    reason="macOS process smoke; run with PENPAL_RUN_SLOW=1",
)
def test_real_serve_process_stop_resume_and_kill(temporary_home, tmp_path, monkeypatch):
    from penpal.store.db import init_db

    home = temporary_home
    data_dir = home / ".penpal"
    data_dir.mkdir(parents=True)
    config_path = tmp_path / "runtime.toml"
    config_path.write_text(
        """[app]
db_path = "~/.penpal/penpal.db"
log_path = "~/.penpal/penpal.log"
flush_interval_sec = 1

[watchers]
window = false
idle = false
clipboard = false
manual = false
keyboard_chunk = false
browser = false
browser_url = false
ax_text = false
ocr = false

[sources.scheduler]
enabled = false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("PENPAL_RUNTIME_RECORD_INTERVAL_SEC", "0.5")
    monkeypatch.setenv("PENPAL_RUNTIME_DISABLE_LAUNCHD_PROBE", "1")
    monkeypatch.setattr(EVALUATOR, "HUNG_AFTER_SEC", 1.5)
    monkeypatch.setattr(EVALUATOR, "STARTING_GRACE_SEC", 1.5)
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    init_db(data_dir / "penpal.db")

    process = subprocess.Popen(
        [sys.executable, "-m", "penpal.cli", "serve", "--config", str(config_path)],
        cwd=tmp_path,
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        record = None
        while time.monotonic() < deadline:
            record = read_record()
            if record is not None:
                break
            time.sleep(0.1)
        assert record is not None
        from penpal.runtime.launchd import process_start_time
        assert record.process_start_time == process_start_time(record.pid), (record.process_start_time, process_start_time(record.pid))
        assert evaluate().state is RuntimeState.RUNNING

        os.kill(process.pid, signal.SIGSTOP)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and evaluate().state is not RuntimeState.HUNG:
            time.sleep(0.1)
        assert evaluate().state is RuntimeState.HUNG

        os.kill(process.pid, signal.SIGCONT)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and evaluate().state is not RuntimeState.RUNNING:
            time.sleep(0.1)
        assert evaluate().state is RuntimeState.RUNNING

        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        assert evaluate().state is RuntimeState.NOT_RUNNING
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGCONT)
            process.kill()
            process.wait(timeout=5)
