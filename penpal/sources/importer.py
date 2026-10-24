"""Batched import with a persisted resume point (issue #32, AC2).

registry.read_all() stores a whole source in one pass and commits the cursor only at the end, so a stop in the
middle loses the resume point. import_source() takes `batch_size` accepted events at a time, stores them, and
only then commits the plugin cursor. The saved cursor therefore never points past stored events, and events
of finished batches are queryable before the import ends.

Progress lives in app_state under "import_progress:<source>" so `penpal status` and the HUD can show it.

Resume is exact for plugins with a live cursor (claude_code). Other plugins re-read their window on the next
run; the unique index on raw_events (source_id, external_id) and (source, ts_start, content_hash) stops that
from creating duplicate rows.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator

from penpal.capture.capture_control import source_writes_allowed
from penpal.config import Config
from penpal.sources.availability import PROBE_ERROR, Availability, unavailable
from penpal.sources.cleaning.config import load_cleaning_config
from penpal.sources.cleaning.dedup import dedup_events
from penpal.sources.registry import accept_event, get_source, list_sources
from penpal.sources.sink import persist_semantic_event
from penpal.sources.types import DataSource, SemanticEvent
from penpal.store.repository import get_state, set_state
from penpal.utils.logging import get_logger

LOGGER = get_logger("sources.importer")

DEFAULT_BATCH_SIZE = 200
PROGRESS_KEY_PREFIX = "import_progress:"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_INTERRUPTED = "interrupted"
STATE_FAILED = "failed"
_RESUMABLE_STATES = frozenset({STATE_RUNNING, STATE_INTERRUPTED, STATE_FAILED})


@dataclass
class ImportResult:
    source: str
    status: str  # "done" | "failed" | "skipped" (reason set) | "interrupted" (raised out of import_source)
    reason: str | None = None  # skip cause: an availability reason code, or "switched_off"
    batches: int = 0  # batches stored and committed in this run
    events: int = 0  # events handed to storage in this run
    resumed: bool = False  # an earlier run stopped here, so this run continued from its saved cursor


def import_source(
    name: str,
    *,
    since: datetime,
    until: datetime,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Callable[[ImportResult], None] | None = None,
) -> ImportResult:
    """Import one registered source in batches. `on_batch` runs after each committed batch; tests use it to
    simulate an interruption (an exception raised there is recorded as "interrupted" and re-raised)."""
    plugin = get_source(name)
    if plugin is None:
        raise ValueError(f"unknown source: {name}")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if not source_writes_allowed(name):
        return ImportResult(source=name, status="skipped", reason="switched_off")
    state = _probe(plugin)
    if not state.available:
        return ImportResult(source=name, status="skipped", reason=state.reason)

    prior = read_progress(name) or {}
    resumed = prior.get("state") in _RESUMABLE_STATES and int(prior.get("events") or 0) > 0
    result = ImportResult(source=name, status=STATE_RUNNING, resumed=resumed)
    cleaning_config = load_cleaning_config()
    deny_hosts = Config.load().privacy.url_deny_hosts
    stream = _accepted_events(plugin, since, until, cleaning_config)
    _save_progress(result, STATE_RUNNING)

    try:
        while True:
            batch = list(itertools.islice(stream, batch_size))
            if not batch:
                break
            batch = _dedup_batch(batch, cleaning_config)
            failed = False
            for event in batch:
                try:
                    persist_semantic_event(event)
                except Exception as exc:
                    LOGGER.warning("import persist failed source=%s ref=%s: %s", name, event.raw_ref, exc)
                    failed = True
                    break
            _commit(plugin, failed=failed)
            if failed:
                result.status = STATE_FAILED
                _save_progress(result, STATE_FAILED)
                return result
            result.batches += 1
            result.events += len(batch)
            _save_progress(result, STATE_RUNNING)
            if on_batch is not None:
                on_batch(result)
    except BaseException:
        result.status = STATE_INTERRUPTED
        _save_progress(result, STATE_INTERRUPTED)
        raise

    result.status = STATE_DONE
    _save_progress(result, STATE_DONE)
    return result


def read_progress(name: str) -> dict | None:
    """Last recorded run for `name`, or None. A corrupt record reads as None (never raises into status)."""
    try:
        raw = get_state(PROGRESS_KEY_PREFIX + name)
        value = json.loads(raw) if raw else None
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def progress_rows() -> list[tuple[str, dict]]:
    """(source, last run) for every registered source that has one, in name order."""
    rows: list[tuple[str, dict]] = []
    for plugin in list_sources():
        record = read_progress(plugin.name)
        if record is not None:
            rows.append((plugin.name, record))
    return rows


def _probe(plugin: DataSource) -> Availability:
    try:
        return plugin.availability()
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("availability failed for %s: %s", plugin.name, exc)
        return unavailable(PROBE_ERROR)


def _accepted_events(
    plugin: DataSource, since: datetime, until: datetime, cleaning_config
) -> Iterator[SemanticEvent]:
    # Pulled lazily: islice stops this generator right after the last accepted event of a batch, so a
    # live cursor is never ahead of the events that were handed to storage.
    for instance in plugin.discover():
        try:
            stream = plugin.read(instance, since, until)
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("read setup failed for %s (%s): %s", plugin.name, instance.locator, exc)
            continue
        for event in stream:
            if accept_event(plugin.name, event, cleaning_config, deny_hosts):
                yield event


def _dedup_batch(batch: list[SemanticEvent], cleaning_config) -> list[SemanticEvent]:
    # Dedup works within a batch only; across batches the storage unique indexes still hold.
    try:
        deduped = dedup_events(batch, time_window_minutes=cleaning_config.dedup_time_window_minutes)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("dedup failed in import batch: %s", exc)
        return batch
    deduped.sort(key=lambda event: event.time)
    return deduped


def _commit(plugin: DataSource, *, failed: bool) -> None:
    commit = getattr(plugin, "commit_cursors", None)
    if commit is None:
        return
    try:
        commit(failed=failed)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.warning("cursor commit failed for %s: %s", plugin.name, exc)


def _save_progress(result: ImportResult, state: str) -> None:
    record = {
        "state": state,
        "batches": result.batches,
        "events": result.events,
        "resumed": result.resumed,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        set_state(PROGRESS_KEY_PREFIX + result.source, json.dumps(record, sort_keys=True))
    except Exception as exc:  # pragma: no cover - progress is informational; never fail the import for it
        LOGGER.warning("could not save import progress for %s: %s", result.source, exc)


__all__ = ["DEFAULT_BATCH_SIZE", "ImportResult", "import_source", "progress_rows", "read_progress"]
