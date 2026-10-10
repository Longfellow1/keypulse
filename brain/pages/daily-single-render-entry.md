---
id: daily-single-render-entry
title: Daily output has one write path and one renderer
category: decision
status: archived
created: "2026-10-10T10:26:12"
updated: "2026-10-10T14:52:53"
---

<!-- compiled_truth -->
## Decision
All daily writes go through one exporter plus CLI entry point. Six historical renderers were deleted (things, narrative, narrative_v2, skeleton, write, decisions).

## Rationale
Multiple renderers drifted apart. A single entry point makes section-name and quality contracts enforceable.

## Consequences
- HUD and weekly consume the new section-name contract (19aaaea).
- Section names have a single source (398574d); the render layer forces `cluster.display_name` over anchor display (80a4285).
- Changing output means changing the one path. Do not add a second writer.

## Blast radius
`keypulse/pipeline/daily_orchestrator.py`, `artifact_writer.py`, `obsidian/exporter`, `hud/`.


## Timeline

- time: 2026-10-10T10:26:12
  kind: decision
  summary: "Created this page: Daily output has one write path and one renderer"
  source: "commits 9151b68 (B, 2026-05-10), 6896c20 (C, 2026-05-10), 19aaaea (D); docs/说明-refactor-daily-renderer-unification.md"
  affects: [daily-single-render-entry]

- time: 2026-10-10T10:26:12
  kind: decision
  summary: captured from project history
  source: "commits 9151b68 (B, 2026-05-10), 6896c20 (C, 2026-05-10), 19aaaea (D); docs/说明-refactor-daily-renderer-unification.md"
  affects: [daily-single-render-entry]

- time: 2026-10-10T14:52:53
  kind: reversal
  summary: "Obsolete: daily report removed from V1 (D24)."
  source: brain archive-page
  affects: [daily-single-render-entry]
