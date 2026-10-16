---
id: retention-keep-forever
title: "D32: No automatic deletion; all memory is kept until the user deletes it"
category: decision
status: active
tags: [retention, data-lifecycle, privacy]
created: "2026-10-13T15:14:00"
updated: "2026-10-16T17:27:17"
---

<!-- compiled_truth -->
Decision (user, 2026-10-13): KeyPulse does not delete anything automatically. All sources, including raw streams such as keyboard chunks, are kept until the user deletes them. Deletion is a user action (CLI purge now; per-source delete in the M3 Control Center).

Why: the daemon ran `apply_retention(retention_days=30)` at every start, deleting raw_events by `created_at` (import time). On the 2026-10-13 snapshot one restart would have removed 4,478 rows (12%), including imported Codex history and eval gold rows, and the loss grows each day the daemon is off. A 2026-04 backup held ~6.8k Terminal and ~3.5k Safari window rows; today the earliest created_at is 2026-08-26, so earlier history was probably lost the same way (not proven). Silent deletion contradicts the core promise that memory outlives its source.

Options rejected: "memory forever, raw streams 30 days by event time" (recommended by the tech lead, declined by the user for simplicity); "30 days by event time" (deletes imported history soon after import).

Consequences: no code path may delete user rows without an explicit user command. The `retention_days` setting must not trigger deletion. Keyboard rows are still kept but hidden from AI by default (earlier decision). Revisit only with the M3 Control Center's per-source controls.


## Timeline

- time: 2026-10-13T15:14:00
  kind: decision
  summary: "Created this page: D32: No automatic deletion; all memory is kept until the user deletes it"
  source: user 2026-10-13
  affects: [retention-keep-forever]

- time: 2026-10-13T15:14:00
  kind: decision
  summary: "keep forever, no auto retention"
  source: user 2026-10-13
  affects: [retention-keep-forever]

- time: 2026-10-13T15:24:20
  kind: note
  summary: "Scope (2026-10-13, PR #81): D32 removes runtime deletion (daemon startup retention). Historical one-time migrations stay as written (v17 exact-duplicate dedupe, v24 privacy hygiene gate drops, v26 noise cleanup): they already ran on existing DBs, and v24 enforces what should never have been stored (privacy), which is not retention. Any NEW migration or code path that deletes user rows needs an explicit user decision."
  source: tech lead review
  affects: [retention-keep-forever]

- time: 2026-10-16T17:27:17
  kind: note
  summary: "D34 (2026-10-16) adds automatic cleanup for non-memory artifacts only (backups, caches, logs, eval output); memory rows remain under D32. See [[data-footprint-limits]]"
  source: user 2026-10-16
  affects: [retention-keep-forever, data-footprint-limits]
