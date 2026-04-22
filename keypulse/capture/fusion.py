from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import replace

from keypulse.capture.merge import parse_ts, similarity_ratio, source_priority
from keypulse.store.models import RawEvent


@dataclass(frozen=True)
class FusionResult:
    persist: bool
    event: RawEvent | None
    reason: str | None = None


@dataclass
class _CanonicalRecord:
    event: RawEvent


class CaptureFusionEngine:
    def __init__(self, similarity_threshold: float = 0.92, window_sec: float = 15.0):
        self._threshold = similarity_threshold
        self._window_sec = window_sec
        self._canonicals: list[_CanonicalRecord] = []

    def fuse(self, event: RawEvent) -> FusionResult:
        if not event.content_text or source_priority(event.source) == 0:
            return FusionResult(persist=True, event=event)

        matched = self._find_match(event)
        if matched is None:
            self._canonicals.append(_CanonicalRecord(event=event))
            return FusionResult(persist=True, event=event)

        current_priority = source_priority(event.source)
        matched_priority = source_priority(matched.event.source)
        if current_priority <= matched_priority:
            return FusionResult(
                persist=False,
                event=None,
                reason="lower_priority_duplicate",
            )

        merged_event = self._merge_metadata(matched.event, event)
        matched.event = merged_event
        return FusionResult(
            persist=True,
            event=merged_event,
            reason="replaced_lower_priority_duplicate",
        )

    def _find_match(self, event: RawEvent) -> _CanonicalRecord | None:
        event_ts = parse_ts(event.ts_start)
        for record in reversed(self._canonicals):
            delta = abs((event_ts - parse_ts(record.event.ts_start)).total_seconds())
            if delta > self._window_sec:
                continue
            if event.app_name != record.event.app_name:
                continue
            if event.window_title != record.event.window_title:
                continue
            if similarity_ratio(event.content_text, record.event.content_text) >= self._threshold:
                return record
        return None

    def _merge_metadata(self, previous: RawEvent, current: RawEvent) -> RawEvent:
        payload: dict[str, object] = {}
        for candidate in (previous.metadata_json, current.metadata_json):
            if not candidate:
                continue
            try:
                payload.update(json.loads(candidate))
            except Exception:
                continue

        source_chain: list[str] = []
        for candidate in (payload.get("sources"), [previous.source], [current.source]):
            if isinstance(candidate, list):
                for source in candidate:
                    if isinstance(source, str) and source not in source_chain:
                        source_chain.append(source)

        payload["sources"] = source_chain
        payload["merge_reason"] = "replaced_lower_priority_duplicate"
        return replace(current, metadata_json=json.dumps(payload))
