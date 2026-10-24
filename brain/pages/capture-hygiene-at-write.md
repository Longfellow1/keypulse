---
id: capture-hygiene-at-write
title: "D27: capture hygiene restored at write time; keyboard cleaned (EN), Chinese via AX spike; loop marked"
category: decision
status: active
tags: [v1, capture, privacy, pipeline, decision]
created: "2026-10-12T11:02:04"
updated: "2026-10-24T12:20:18"
---

<!-- compiled_truth -->
<current best understanding — replace this with the real content>

## Timeline

- time: 2026-10-12T11:02:04
  kind: decision
  summary: "Created this page: D27: capture hygiene restored at write time; keyboard cleaned (EN), Chinese via AX spike; loop marked"
  source: owner 2026-10-12
  affects: [capture-hygiene-at-write]

- time: 2026-10-12T11:10:31
  kind: decision
  summary: "Additions from the OSS review (Recall, screenpipe, ActivityWatch). Write-time gate: configurable window-title deny list; browser internal pages dropped; private/incognito windows metadata-only. MCP output: second redact pass, failing closed (a uniform error, never raw text). CN ID and bank-card detectors fixed for CJK-adjacent text, now validated by GB11643 and Luhn checksums. Self-reference loop handling has no external precedent, so it must be validated on real sessions."
  source: oss-cleaning-research 2026-10-12
  affects: [capture-hygiene-at-write]

- time: 2026-10-24T12:14:21
  kind: decision
  summary: "#26 B6: url_deny_hosts also gates the sources ingestion path (chrome/safari history) in sources registry read_all, before storage or CLI output. Before this, history rows from denied domains were stored. Covered by tests/test_issue26_capture_chain.py. Open: the sources path does not apply app blacklist or chat-app defaults."
  source: "issue #26, 2026-10-24"
  affects: [capture-hygiene-at-write]

- time: 2026-10-24T12:20:18
  kind: decision
  summary: "#26: the excluded-domain list now covers keyboard, AX and clipboard text too. The manager tracks the last URL each browser app showed (from browser_url / browser events, before any drop) and the shared gate returns metadata-only denied_domain for text from a browser on an excluded host. Unknown URL is not blocked. Caveat: the URL watchers are off by default (watchers.browser_url = false), so this takes effect only when they are enabled."
  source: "issue #26 follow-up, 2026-10-24"
  affects: [capture-hygiene-at-write]
