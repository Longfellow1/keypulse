---
id: keyboard-capture-p0-experimental
title: "Keyboard capture stays P0 (opt-in, filtered text chunks) during the experimental phase"
category: decision
status: active
tags: [scope, capture, privacy]
created: "2026-10-10T11:19:05"
updated: "2026-10-24T12:06:19"
---

<!-- compiled_truth -->
## Decision
Keyboard capture remains **P0**: **on by default**, filtered text chunks only (never per-keystroke).
- Chunks (`keyboard_chunk`) are **searchable by AI like other sources**, within the buckets a Host is granted (editors / terminals map to Work). Only legacy garbled rows captured before schema v24 are hidden from search (`pre_fix_keyboard`).
- Why: keyboard captures exactly what bash cannot recover later (D25 #1), and eval recall relies on it.
- Privacy relies on per-Host bucket grants, exclusion lists, pre-storage redaction, a one-time risk notice at install, and one-command off (`penpal source disable keyboard_chunk`). Not on opt-in or search exclusion.
- Experimental phase: result quality comes first; deeper redaction is best-effort P1 hardening.

## Alternatives
- Demote keyboard capture to P1 (assistant's recommendation, 2026-10-10). Rejected by the owner.
- Capture keyboard but exclude it from search by default (2026-10-14 wording, issue #29 AC3). Reversed 2026-10-24.
- Explicit opt-in / default off (original D13). Superseded by default-on (2026-10-14).

## Consequence
- Revisit redaction strength before any non-experimental release.
- AI echo answers (D27) remain tagged and excluded from search; that is separate from keyboard.
- Related: [[v1-deferred-undecided-items]], [[keypulse-v1-local-memory-infra]].


## Timeline

- time: 2026-10-10T11:19:05
  kind: decision
  summary: "Created this page: Keyboard capture stays P0 (opt-in, filtered text chunks) during the experimental phase"
  source: "Owner decision 2026-10-10 (D-a)"
  affects: [keyboard-capture-p0-experimental]

- time: 2026-10-10T11:19:05
  kind: decision
  summary: "Owner decision D-a: keyboard capture kept at P0; redaction is best-effort P1 hardening"
  source: Owner decision 2026-10-10
  affects: [keyboard-capture-p0-experimental]

- time: 2026-10-14T10:31:31
  kind: decision
  summary: "2026-10-14 owner: keyboard capture stays ON by default (keeps D26 'all streams open incl. keyboard' and 'captured but tagged, excluded from search by default'). Issue #29 is rewritten from 'default off' to 'stays on + clear risk notice + easy off switch'."
  source: owner 2026-10-14
  affects: [keyboard-capture-p0-experimental]

- time: 2026-10-24T12:06:13
  kind: reversal
  summary: "Keyboard chunks are on by default and searchable within granted buckets like other sources; the earlier 'excluded from search by default' wording (2026-10-14 entry, issue #29 AC3) is reversed. Only pre-v24 garbled rows stay hidden. Privacy relies on bucket grants, exclusion lists, redaction, one-time risk notice and one-command off."
  source: "Owner decision 2026-10-24 (option A)"
  affects: [keyboard-capture-p0-experimental]

- time: 2026-10-24T12:06:19
  kind: decision
  summary: "Compiled truth: keyboard is default-on and searchable within granted buckets (owner 2026-10-24), replacing opt-in/excluded-from-search wording"
  source: Owner decision 2026-10-24
  affects: [keyboard-capture-p0-experimental]
