---
id: legacy-data-new-schema-importer
title: "New clean schema plus one-time importer from ~/.keypulse/keypulse.db; legacy gets bug fixes only"
category: decision
status: active
tags: [data, migration, legacy]
created: "2026-10-10T10:51:08"
updated: "2026-10-10T10:51:08"
---

<!-- compiled_truth -->
## Decision
- The new core uses a **new clean schema**, not the legacy schema.
- A **one-time importer** reads `~/.keypulse/keypulse.db` and maps data into the new schema.
- Legacy daily / weekly / Obsidian code receives **bug fixes only, no new features**.

## Rationale
- The legacy schema is split in ownership (`store/migrations.py` SCHEMA_VERSION 19 vs `pipeline/triggers.py` llm_trigger_log and `pipeline/run_record.py` run_records), see [[legacy-extraction-blockers]]. Reusing it would carry that split into the new core.

## Consequence
- Importer is a one-off, so its mapping is only needed once. Legacy modules stay in the repo under [[refactor-in-place-with-legacy-tag]] baseline rules.


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
