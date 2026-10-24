---
id: tcc-permission-refresh-on-install
title: Reinstall and onboarding must kick the daemon to refresh TCC
category: decision
status: active
created: "2026-10-10T10:26:13"
updated: "2026-10-24T23:56:02"
---

<!-- compiled_truth -->
## Decision
After onboarding, the daemon is kicked so the TCC cache refreshes. Re-signing changes the cdhash; a reinstall with the same bundle id can leave the permission toggle on but ineffective.

## Gotchas
- Re-signing an app after install requires re-granting, or a daemon restart at minimum.
- `make tcc-reset` exists for manual reset (`Makefile`).
- Permission state is partly unknowable from code (see [[capability-fact-vs-probe]]).


## Timeline

- time: 2026-10-10T10:26:13
  kind: decision
  summary: "Created this page: Reinstall and onboarding must kick the daemon to refresh TCC"
  source: "commits b37417f (2026-05-25), 802bf63 (2026-06-01); Makefile onboard/post-onboard-kick"
  affects: [tcc-permission-refresh-on-install]

- time: 2026-10-10T10:26:13
  kind: decision
  summary: captured from project history
  source: "commits b37417f (2026-05-25), 802bf63 (2026-06-01); Makefile onboard/post-onboard-kick"
  affects: [tcc-permission-refresh-on-install]

- time: 2026-10-24T23:56:02
  kind: decision
  summary: "D36: the daemon kick after reinstall/onboarding now goes through the single runtime control path (penpal.runtime.control restart)."
  source: docs/design/runtime-contract.md
  affects: [runtime-contract]
