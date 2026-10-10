---
id: beat-vs-emit-separation
title: Watcher heartbeat and real event emission are tracked separately
category: decision
status: active
created: "2026-10-10T10:26:13"
updated: "2026-10-10T10:26:13"
---

<!-- compiled_truth -->
## Decision
Two timestamps per watcher: `_last_emit_at_mono` for a real RawEvent push (used for silent-timeout detection and capability overrides) and `_last_beat_at_mono` for any thread heartbeat.

## Why
A live thread with no real events looked healthy. The HUD watcher alert was a false positive until it switched to heartbeat-based liveness (26fd7d4).

## Gotcha
Do not merge these fields. browser_url self-heal uses heartbeat plus a blacklist cool-down (fc2f2a1).


## Timeline

- time: 2026-10-10T10:26:13
  kind: decision
  summary: "Created this page: Watcher heartbeat and real event emission are tracked separately"
  source: "keypulse/capture/base.py (_last_emit_at_mono vs _last_beat_at_mono); commits fc2f2a1 (2026-05-23), 26fd7d4"
  affects: [beat-vs-emit-separation]

- time: 2026-10-10T10:26:13
  kind: decision
  summary: captured from code comments and history
  source: "keypulse/capture/base.py (_last_emit_at_mono vs _last_beat_at_mono); commits fc2f2a1 (2026-05-23), 26fd7d4"
  affects: [beat-vs-emit-separation]
