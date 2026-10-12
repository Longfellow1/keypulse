---
id: capture-hygiene-at-write
title: "D27: capture hygiene restored at write time; keyboard cleaned (EN), Chinese via AX spike; loop marked"
category: decision
status: active
tags: [v1, capture, privacy, pipeline, decision]
created: "2026-10-12T11:02:04"
updated: "2026-10-12T11:10:31"
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
