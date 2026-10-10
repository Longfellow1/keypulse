---
id: legacy-data-new-schema-importer
title: "New clean schema plus one-time importer from ~/.keypulse/keypulse.db; legacy gets bug fixes only"
category: decision
status: archived
tags: [data, migration, legacy]
created: "2026-10-10T10:51:08"
updated: "2026-10-10T13:53:13"
---

<!-- compiled_truth -->
## Status: REVERSED by D20 (2026-10-10). Kept for history only.
The earlier decision (new clean schema + one-time importer from `~/.keypulse/keypulse.db`) is overturned. See [[v1-build-by-legacy-extraction]].

## Current decision (D20)
- Evolve the legacy SQLite DB in place via migration v20+.
- Add `source_id`, `external_id`, `bucket`, `kind` to `raw_events`. New tables: Source, Evidence, Memory, AccessGrant, SyncCursor, audit log.
- No new schema. No one-time importer.
- `llm_trigger_log` (pipeline/triggers.py) and `run_records` (pipeline/run_record.py) are created outside migrations. They stay legacy-owned and are not part of the core schema.
- The "legacy gets bug fixes only" rule still applies to daily / weekly / Obsidian. D22 refines it: those are removed from the daemon and kept as optional commands.

## Superseded rationale
- The earlier concern was that legacy schema ownership is split (`store/migrations.py` SCHEMA_VERSION 19 vs the two tables above; see [[legacy-extraction-blockers]]). D20 accepts that split and keeps the two tables legacy-owned, so the core schema stays separate from them.


## Timeline

- time: 2026-10-10T10:51:08
  kind: decision
  summary: "Created this page: New clean schema plus one-time importer from ~/.keypulse/keypulse.db; legacy gets bug fixes only"
  source: Owner decision 2026-10-10
  affects: [legacy-data-new-schema-importer]

- time: 2026-10-10T10:51:08
  kind: decision
  summary: Initial decision recorded
  source: Owner decision 2026-10-10
  affects: [legacy-data-new-schema-importer]

- time: 2026-10-10T13:53:13
  kind: decision
  summary: "REVERSED by D20: no new schema, no one-time importer"
  source: "Owner decision 2026-10-10 (D20)"
  affects: [legacy-data-new-schema-importer]

- time: 2026-10-10T13:53:13
  kind: reversal
  summary: "D20 (owner, 2026-10-10) reverses this decision: evolve the legacy SQLite DB in place via migration v20+; no new schema, no one-time importer"
  source: "Owner decision 2026-10-10 (D20)"
  affects: [legacy-data-new-schema-importer, v1-build-by-legacy-extraction]
