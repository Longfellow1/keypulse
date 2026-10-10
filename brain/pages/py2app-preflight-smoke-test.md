---
id: py2app-preflight-smoke-test
title: Preflight must execute the bundled binary before install
category: decision
status: active
created: "2026-10-10T10:26:13"
updated: "2026-10-10T10:26:13"
---

<!-- compiled_truth -->
## Decision
`make install` runs `inner --help` from the built `.app`. Exit != 0, or stdout missing `Usage:`, aborts the install. The last 8 stderr lines are printed.

## Why
On macOS 26 (Tahoe), any bootstrap error in a py2app stub goes through `NSApplication.sharedApplication()` and aborts silently in dispatch_once. The crash log is the only evidence.

## Gotcha
Do not remove the smoke test. Other bootstrap failure modes are covered by the M0 locale fix (`_force_utf8_locale()` before any keypulse import). The smoke test is the general backstop.


## Timeline

- time: 2026-10-10T10:26:13
  kind: decision
  summary: "Created this page: Preflight must execute the bundled binary before install"
  source: "EVOLUTION 2026-05-15 (N5); scripts/preflight_app.py smoke_test_inner_binary"
  affects: [py2app-preflight-smoke-test]

- time: 2026-10-10T10:26:13
  kind: decision
  summary: captured from project history
  source: "EVOLUTION 2026-05-15 (N5); scripts/preflight_app.py smoke_test_inner_binary"
  affects: [py2app-preflight-smoke-test]
