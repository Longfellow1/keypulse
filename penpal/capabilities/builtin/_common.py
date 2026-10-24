from __future__ import annotations

import time

from penpal.runtime import RuntimeState, evaluate, watcher_recently_emitted


def now_ts() -> float:
    return time.time()


def watcher_healthy(name: str) -> bool:
    component = evaluate().components.get(name)
    return component is not None and component.state == "running"


def watcher_has_recent_emit(name: str) -> bool:
    return watcher_recently_emitted(evaluate(), name)


def capture_pipeline_healthy(required_watchers: list[str]) -> bool:
    status = evaluate()
    return status.state == RuntimeState.RUNNING and all(
        status.components.get(name) is not None and status.components[name].state == "running"
        for name in required_watchers
    )
