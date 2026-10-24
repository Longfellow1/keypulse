from __future__ import annotations

import json
import queue
import threading
import time

from penpal.hud.controller import HudModelWorker


class SlowController:
    def __init__(self):
        self.started = threading.Event()
        self.calls = 0

    def model(self, *, fresh=False):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            time.sleep(5)
        return {"tabs": [{"badge": "amber"}], "title": "worker model"}

    def dispatch(self, action, args=None):
        return self.model(fresh=True)


def test_slow_reader_does_not_block_main_thread_callback_and_applies_finished_model():
    controller = SlowController()
    main_callbacks: queue.Queue = queue.Queue()
    applied = []
    main_thread = threading.get_ident()
    worker = HudModelWorker(
        controller,
        main_dispatcher=main_callbacks.put,
        apply_script=lambda script, badge: applied.append((script, badge, threading.get_ident())),
    )

    worker.refresh(fresh=True)
    assert controller.started.wait(1)

    started_at = time.perf_counter()
    worker.refresh(fresh=True)  # the main-thread NSTimer callback only schedules work
    assert time.perf_counter() - started_at < 0.1, "timer callback waited for the slow reader"

    apply = main_callbacks.get(timeout=6)
    assert applied == []  # dispatcher queued the finished model for the main thread
    apply()
    assert len(applied) == 1
    script, badge, applied_thread = applied[0]
    assert badge == "amber" and applied_thread == main_thread
    payload = script.removeprefix("window.render(").removesuffix(");")
    model = json.loads(payload)
    assert model["title"] == "worker model" and model["model_built_at"]
