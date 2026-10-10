---
id: v1-development-baseline-prd
title: "docs/KeyPulse_V2.md (V1 PRD) is the formal development baseline, superseding KeyPulse_V1_PRD.md"
category: decision
status: active
tags: [prd, v1, baseline]
created: "2026-10-10T11:19:06"
updated: "2026-10-10T11:30:16"
---

<!-- compiled_truth -->
## Decision
- `docs/KeyPulse_V2.md` (titled "V1 PRD") is the **formal development baseline**. After its pending revision it becomes **v1.2**.
- It supersedes `docs/KeyPulse_V1_PRD.md`.
- **Filename is NOT renamed for now.** Content is the V1 PRD v1.2 baseline and the header states it. Rename later if desired; update brain references then.

## PRD v1.2 review revisions (B1-B8): APPROVED
- B1: `coverage.note` must not reveal Private content.
- B2: examples must not depend on Feishu. Feishu / WeChat are the V1.1 pilot.
- B3: macOS-only, unchanged.
- B4: agent-written Memory defaults to `declared`. It cannot cite unreadable Evidence. It is bucketed within the Grant.
- B5: the keyboard-capture toggle UI must disclose best-effort filtering and possible cloud egress.
- B6: the user's App/domain exclusion list is NOT "desensitization". It must work (AT18). KeyPulse's own token is never written to logs or audit.
- B7: `get_memory` stays P1. Memory hits are returned via `search_context` in P0.
- B8: AT12 is up to P0. Add AT15-AT19. AT07 is non-blocking.

## Conflict resolutions
1. `get_memory` stays P1 (B7 workaround accepted).
2. "No passwords in logs" is best-effort. KeyPulse's own tokens and credentials are a hard ban.
3. "Unauthorised-access leakage = 0" stays a **HARD release gate**. It is a security regression check, not an effectiveness metric. The Eval PRD must not relax it.
4. Cursor: P0 as an MCP host. Parser-as-data-source stays P1.
5. Filename: see Decision above.

## Resolutions carried by this baseline
- Platform: **macOS only** for V1. Windows is later (see [[v1-deferred-undecided-items]]).
- Privacy buckets: defaults per PRD §3.4.2. Work and Personal are open to authorised agents. Private and Financial are denied by default.
- Feishu / WeChat: **V1.1 pilot**. No special casing in the data model.

## Still open
- Personal-chat capture details (beyond the V1.1 pilot scope).
- Camera capture: not yet specified.
- Windows: deferred, details not set.
- Cursor data-source format: not specified in the PRD. To confirm.

## Consequence
- Legacy design choices that conflict with v1.2 are not carried forward. See [[refactor-in-place-with-legacy-tag]].
- Related: [[keypulse-v1-local-memory-infra]], [[eval-prd-separate]], [[mcp-caller-auth-p0]], [[keyboard-capture-p0-experimental]].


## Timeline

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Created this page: docs/KeyPulse_V2.md (V1 PRD) is the formal development baseline, superseding KeyPulse_V1_PRD.md"
  source: "Owner decision 2026-10-10 (D-d)"
  affects: [v1-development-baseline-prd]

- time: 2026-10-10T11:19:06
  kind: decision
  summary: "Owner decision D-d: docs/KeyPulse_V2.md is the formal baseline (to become v1.2), supersedes docs/KeyPulse_V1_PRD.md"
  source: Owner decision 2026-10-10
  affects: [v1-development-baseline-prd]

- time: 2026-10-10T11:30:16
  kind: decision
  summary: "Record owner-approved PRD v1.2 review (B1-B8), resolved conflicts, and remaining open items"
  source: "Owner delegated to assistant recommendation, 2026-10-10"
  affects: [v1-development-baseline-prd]
