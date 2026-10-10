---
id: v1-build-by-legacy-extraction
title: "V1 builds by legacy extraction: reuse first, new build only where legacy lacks it (D20-D23)"
category: decision
status: active
tags: [v1, refactor, legacy, decision]
created: "2026-10-10T13:53:09"
updated: "2026-10-10T13:53:09"
---

<!-- compiled_truth -->
## Principle (owner, 2026-10-10)
Maximise reuse. Extract from the legacy code and align it both ways with the new product goals. Only genuinely missing pieces are new builds (MCP, grants, memory, audit).

## Decisions
- **D20 Database:** evolve the legacy SQLite DB in place via migration v20+. Add `source_id`, `external_id`, `bucket`, `kind` to `raw_events`; add new tables for Source, Evidence, Memory, AccessGrant, SyncCursor and the audit log. No new schema and no one-time importer. `llm_trigger_log` and `run_records` stay legacy-owned and are not core schema.
- **D21 Code organisation:** slim in place. `store` / `search` / `sources` / `capture` / `privacy` / `hud` stay where they are. Cut the ~10 coupling edges to pipeline / obsidian / quality / LLM. import-linter forbids core modules from importing those four, and forbids macOS-only libs outside capture / hud / startup (D18). New capabilities go in new modules. No top-level "core" package.
- **D22 Legacy daily / weekly / Obsidian:** removed from the background daemon. Kept as optional standalone commands or scheduled jobs, bug fixes only. The daemon runs core only (capture, source sync, search, MCP).
- **D23 P0 aligned to legacy:** Artifact object goes to P1 (P0 uses the existing `artifact` field in event metadata). Session in P0 = legacy window-focus sessions only. Buckets in P0 = per-source default + app rules, reusing the legacy `policies` table, with no content classification; Temporary and its expiry go to P1. Control surface in P0 = global pause + per-source switch, and delete by source and by time range (this also fixes the legacy `purge --app` over-delete bug). Timed pause and delete by bucket / single item go to P1. The "deletion must be complete" gate (AT10 / G4) is unchanged.

## Why
- Legacy store, search, privacy, capture manager and source parsers are already built and tested (legacy-v0 baseline: 1215 passed). Rebuilding them wastes time and adds risk.
- The new goals with no legacy equivalent (MCP server, access grants, memory commit, audit) are the only real new work.
- Aligning P0 to legacy keeps scope from growing beyond what the existing code can deliver.

## Conditions
- The reuse only holds after the cut list in [[legacy-extraction-blockers]] is done, and the import-linter contract is in CI.
- Legacy bugs found in the audit are fixed as part of extraction, not carried forward.

## Related
- [[legacy-extraction-blockers]]
- [[refactor-in-place-with-legacy-tag]]
- [[legacy-data-new-schema-importer]] (reversed by D20)
- [[v1-development-baseline-prd]]


## Timeline

- time: 2026-10-10T13:53:09
  kind: decision
  summary: "Created this page: V1 builds by legacy extraction: reuse first, new build only where legacy lacks it (D20-D23)"
  source: created via brain create-page
  affects: [v1-build-by-legacy-extraction]

- time: 2026-10-10T13:53:09
  kind: decision
  summary: Recorded D20-D23 reuse-first V1 build route
  source: "Owner decision 2026-10-10 (D20-D23)"
  affects: [v1-build-by-legacy-extraction]
