---
id: capability-fact-vs-probe
title: Capability status is split into fact and probe layers
category: decision
status: active
created: "2026-10-10T10:26:13"
updated: "2026-10-10T10:26:13"
---

<!-- compiled_truth -->
## Decision
Capabilities are split into **facts** (hard evidence) and **probes** (for example AX or screen probes). Unreliable probes can no longer poison the top-level error code. HUD top status shows business delivery state; capability problems are secondary hints.

## Why
AX probes gave false positives that made the HUD report errors while data was flowing.

## Gotcha
A fact override must cover every watcher it affects. Incident 2026-05-17 (`docs/记录-incident-2026-05-17-selfheal-loop.md`): AX was revoked, but keyboard_chunk and ax_text still showed ok=true.


## Timeline

- time: 2026-10-10T10:26:13
  kind: decision
  summary: "Created this page: Capability status is split into fact and probe layers"
  source: "commit 08cc599 (2026-05-12), fa90da6; EVOLUTION M3 (HUD top status uses business delivery state)"
  affects: [capability-fact-vs-probe]

- time: 2026-10-10T10:26:13
  kind: decision
  summary: captured from project history
  source: "commit 08cc599 (2026-05-12), fa90da6; EVOLUTION M3 (HUD top status uses business delivery state)"
  affects: [capability-fact-vs-probe]
