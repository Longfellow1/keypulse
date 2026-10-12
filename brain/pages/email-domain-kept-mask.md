---
id: email-domain-kept-mask
title: "D31: emails masked before @, domain kept, at write and query time; trace back via evidence"
category: decision
status: active
tags: [privacy, redaction, mcp]
created: "2026-10-12T19:42:56"
updated: "2026-10-13T01:10:25"
---

<!-- compiled_truth -->
D31 (2026-10-12, product owner): emails are masked in the local part only, keeping the domain (`***@gmail.com`).

- Applies at write time (desensitizer) and at query time (the MCP second redaction pass over every returned text field), so imported sources such as codex/claude transcripts are covered too. Before D31 the query-time pass only removed secrets, and a real-data replay found an unmasked email returned from a codex_cli row.
- Why: the domain keeps answers useful ("which mailbox / which service"), while the identifying local part never reaches an AI host.
- Traceability: through evidence_id / get_evidence, which returns the original location (path + line) for the user to open themselves. MCP never returns the unmasked email.
- Governed by `privacy.redact_emails` (default on).


## Timeline

- time: 2026-10-12T19:42:56
  kind: decision
  summary: "Created this page: D31: emails masked before @, domain kept, at write and query time; trace back via evidence"
  source: user decision 2026-10-12
  affects: [email-domain-kept-mask]

- time: 2026-10-12T19:42:56
  kind: decision
  summary: Rewrote compiled_truth to the new best understanding
  source: brain update-truth
  affects: [email-domain-kept-mask]
