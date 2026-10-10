---
slug: flow
title: Key flows
role: key flows
updated: "2026-10-10T10:25:44"
---

# Key flows

## Main pipeline (capture to output)
```mermaid
sequenceDiagram
  participant LA as launchd daemon
  participant W as capture watchers / sources
  participant P as privacy
  participant DB as keypulse.db
  participant PL as pipeline (daily orchestrator)
  participant GW as model gateway
  participant QA as validators
  participant AN as anchors.json
  participant OB as Obsidian vault
  participant HUD as HUD
  LA->>W: start watchers (heartbeat + emit)
  W->>P: raw events
  P->>DB: desensitized rows
  LA->>PL: daily run (keypulse daily / obsidian-sync)
  PL->>DB: read day events
  PL->>GW: LLM steps (entity extract, narrative)
  GW-->>PL: JSON per prompt schema
  PL->>QA: quality gates (things >= 3, section names)
  QA-->>PL: pass / repair / reject
  PL->>AN: update anchor state
  PL->>OB: single write entry (exporter + cli)
  PL->>HUD: delivery status
```

## Weekly
`keypulse weekly run` -> weekly orchestrator -> entity merge + L5 main narrative -> `weekly_validator` -> Obsidian `Weekly/`.

## Gotchas
- Daily is gated: `quality_gate` rejects thin output (thing_count < 3); a rejected run leaves the previous-day file visible (seen 2026-05-17, see `docs/记录-incident-2026-05-17-selfheal-loop.md`).
- Silent-failure history: 2026-05-14 `UnicodeDecodeError` removed a day's daily with no HUD error; fixed by M0 locale and M1 RunRecorder delivery ledger.
- TCC permissions: a reinstall changes the code-signing cdhash; onboarding must kick the daemon afterwards (see [[tcc-permission-refresh-on-install]]).
