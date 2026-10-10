---
id: legacy-extraction-blockers
title: "Legacy code extraction blockers and candidates (analysis 2026-10-10)"
category: project
status: active
tags: [refactor, boundary, analysis]
created: "2026-10-10T10:51:09"
updated: "2026-10-10T10:51:09"
---

<!-- compiled_truth -->
## Blockers (facts)
1. `pipeline/__init__.py` has eager imports.
2. `sources/sink.py:11` imports `quality.entity_extractor` and quality cluster strategies, which import `obsidian.layout`.
3. Cycle between `capture` and `capabilities`.
4. `app.py` and `cli.py` are hard-wired to `pipeline` and `obsidian`.
5. DB schema ownership is split: `store/migrations.py` SCHEMA_VERSION 19 vs `pipeline/triggers.py` (llm_trigger_log) and `pipeline/run_record.py` (run_records).

## Extraction candidates
- As-is or light touch: `store`, `search`, `privacy`, `permissions`.
- With refactor first: `capture`, `sources`, `config`, `utils`.

## Rule
Isolation between new core and legacy is enforced by an import-boundary contract, since there is no repo split ([[refactor-in-place-with-legacy-tag]]). Related: [[core-no-embedded-llm]], [[legacy-data-new-schema-importer]].


## Timeline

- time: 2026-10-10T10:51:09
  kind: decision
  summary: "Created this page: Legacy code extraction blockers and candidates (analysis 2026-10-10)"
  source: Code analysis 2026-10-10
  affects: [legacy-extraction-blockers]

- time: 2026-10-10T10:51:09
  kind: decision
  summary: "Initial analysis recorded (facts from code analysis)"
  source: Code analysis 2026-10-10
  affects: [legacy-extraction-blockers]
