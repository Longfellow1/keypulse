"""kill -9 recovery: a daemon killed mid-import / mid-capture must not duplicate or lose rows.

The child processes are real interpreters that SIGKILL themselves after a fixed number of
committed writes, so nothing after that point (no close, no atexit, no cursor commit) runs.
All data is synthetic.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from penpal.capture.manager import CaptureManager
from penpal.capture.normalizer import normalize_clipboard_event, normalize_keyboard_chunk_event
from penpal.config import Config
from penpal.sources.sink import persist_semantic_event
from penpal.sources.types import SemanticEvent
from penpal.store import buckets
from penpal.store.db import close, get_conn, init_db

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

_CHILD = r"""
import os, signal, sys
from pathlib import Path
sys.path.insert(0, {root!r})
sys.path.insert(0, {tests!r})
from penpal.store.db import init_db
db, mode, kill_after = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
init_db(db)
if mode == "source":
    from penpal.sources.sink import persist_semantic_event
    from test_crash_recovery_dedupe import source_events
    for i, ev in enumerate(source_events(12)):
        persist_semantic_event(ev, async_named_entities=False)
        if i + 1 == kill_after:
            os.kill(os.getpid(), signal.SIGKILL)
else:
    from penpal.capture.manager import CaptureManager
    from penpal.config import Config
    from test_crash_recovery_dedupe import live_events
    manager = CaptureManager(Config())
    for i, ev in enumerate(live_events()):
        manager._process_event(ev)
        if i + 1 == kill_after:
            os.kill(os.getpid(), signal.SIGKILL)
"""


def source_events(n: int) -> list[SemanticEvent]:
    """Synthetic imports: half carry a stable external_id (git-style), half rely on the content key."""
    base = datetime(2026, 3, 2, 10, 0, 0, tzinfo=timezone.utc)
    events: list[SemanticEvent] = []
    for i in range(n):
        keyed = i % 2 == 0
        events.append(
            SemanticEvent(
                time=base + timedelta(seconds=30 * i),
                source="git_log" if keyed else "zsh_history",
                actor="user",
                intent=f"synthetic command {i}",
                artifact="shell:zsh",
                raw_ref=f"synthetic:{i}",
                privacy_tier="green",
                metadata={"elapsed_seconds": i},
                external_id=f"synthetic-commit-{i}" if keyed else None,
                source_locator="/synthetic/repo" if keyed else None,
            )
        )
    return events


def live_events():
    ts = "2026-03-02T10:00:{:02d}+00:00"
    return [
        normalize_keyboard_chunk_event("hello world this is a test", app_name="Notes", process_name="com.apple.Notes", ts_start=ts.format(11)),
        normalize_clipboard_event("kubectl", app_name="Notes", ts_start=ts.format(12)),
        normalize_keyboard_chunk_event("the roadmap review is on friday", app_name="Notes", process_name="com.apple.Notes", ts_start=ts.format(13)),
        normalize_clipboard_event("meeting agenda draft", app_name="Notes", ts_start=ts.format(14)),
    ]


def _run_child(db: Path, home: Path, mode: str, kill_after: int) -> subprocess.CompletedProcess:
    script = _CHILD.format(root=str(ROOT), tests=str(TESTS_DIR))
    env = {**os.environ, "HOME": str(home)}
    proc = subprocess.run(
        [sys.executable, "-c", script, str(db), mode, str(kill_after)],
        env=env, cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == -signal.SIGKILL, proc.stderr
    return proc


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    buckets.reset_cache()
    yield h
    close()
    buckets.reset_cache()


def _ids() -> list[int]:
    return [r[0] for r in get_conn().execute("SELECT id FROM raw_events ORDER BY id")]


def test_source_import_killed_midway_rerun_adds_no_duplicates(tmp_path, home) -> None:
    db = tmp_path / "kp.db"
    _run_child(db, home, "source", kill_after=5)

    init_db(db)
    survived = _ids()
    assert len(survived) == 5, "rows committed before kill -9 must survive"

    events = source_events(12)
    for ev in events:
        persist_semantic_event(ev, async_named_entities=False)
    after_rerun = _ids()
    assert len(after_rerun) == 12
    assert after_rerun[: len(survived)] == survived, "committed rows keep their ids; none re-inserted"
    distinct = get_conn().execute("SELECT COUNT(DISTINCT content_hash) FROM raw_events").fetchone()[0]
    assert distinct == 12

    for ev in events:  # a third import is a pure no-op
        persist_semantic_event(ev, async_named_entities=False)
    assert _ids() == after_rerun


def test_live_capture_killed_then_restart_does_not_reemit_stored_events(tmp_path, home) -> None:
    # Reference: what a clean run stores for these events.
    ref_db = tmp_path / "ref.db"
    init_db(ref_db)
    ref = CaptureManager(Config())
    for ev in live_events():
        ref._process_event(ev)
    expected = len(_ids())
    assert expected >= 3
    close()

    db = tmp_path / "kp.db"
    _run_child(db, home, "live", kill_after=2)

    init_db(db)
    survived = _ids()
    assert survived, "events stored before kill -9 must survive"
    restarted = CaptureManager(Config())  # fresh process state after restart
    for ev in live_events():  # watchers re-deliver the same observations
        restarted._process_event(ev)
    assert len(_ids()) == expected
    assert _ids()[: len(survived)] == survived
