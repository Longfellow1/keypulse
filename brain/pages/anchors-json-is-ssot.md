---
id: anchors-json-is-ssot
title: "anchors.json (~/.keypulse) is the single source of truth for anchors"
category: decision
status: active
created: "2026-10-10T10:26:12"
updated: "2026-10-10T10:26:12"
---

<!-- compiled_truth -->
## Decision
`~/.keypulse/anchors.json` is the SSOT for anchors (topics/projects that daily and weekly reports attach to). Obsidian `anchors/<slug>.md` notes are generated from it.

## History
- M1 (189e5cb): model upgrade plus migration of old data.
- M2 (feccf23): daily writes `anchors/<slug>.md`; stopped writing `events/` and `topics/`.
- M4 (353b73b): dropped the Events/Topics scaffolding in the vault.

## Alternatives
Vault notes as SSOT: rejected in code (inferred from the M1-M2 direction; the reasoning is not written down).

## Gotchas
- `anchors.json` accumulates `daily_progress` entries without bound (`pipeline/anchor_gateway.py` note). Growth is unbounded.
- The L0 anchor prompt input was cut from ~120k to ~50k tokens (304149d) to fix `things=0`.
- Cleanup scripts exist (display prefix cleanup 2a0aa24, `--archive-stale-notes` 17714ff); run them through the CLI tooling, not by hand.


## Timeline

- time: 2026-10-10T10:26:12
  kind: decision
  summary: "Created this page: anchors.json (~/.keypulse) is the single source of truth for anchors"
  source: "commits 189e5cb (2026-05-20), feccf23 (2026-05-20), 353b73b (2026-05-21), 304149d; CLAUDE.md-level brief"
  affects: [anchors-json-is-ssot]

- time: 2026-10-10T10:26:12
  kind: decision
  summary: captured from project history
  source: "commits 189e5cb (2026-05-20), feccf23 (2026-05-20), 353b73b (2026-05-21), 304149d; CLAUDE.md-level brief"
  affects: [anchors-json-is-ssot]
