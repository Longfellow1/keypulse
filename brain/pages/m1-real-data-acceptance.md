---
id: m1-real-data-acceptance
title: "D29: M1 signs off on a real-data control eval (KeyPulse vs bash), with a measured 'irreplaceable' criterion"
category: decision
status: active
tags: [eval, m1, acceptance]
created: "2026-10-12T14:41:55"
updated: "2026-10-12T17:27:27"
---

<!-- compiled_truth -->
M1 is signed off on a control eval over the user's own local data, not the synthetic CI set (the synthetic set saturates at recall 1.0 and cannot discriminate).

- Same host (Claude Code headless), same questions, one attempt each: KeyPulse arm (MCP tools only) vs bash arm (shell/file tools only).
- The bash arm is isolated at the OS level (sandbox-exec read deny) from KeyPulse's store, the build session's transcripts and the eval files; prompt-level deny rules alone were bypassed in a first run, which was voided. Eval runs use no session persistence so one question cannot leak into the next.
- The KeyPulse arm runs on a copy of the real DB with transcripts written after question authoring removed.
- The question set, gold answers and results stay on the local machine only (see no-real-data-in-repo).

Criteria: (1) ≥84% correct with cited source on answerable questions; (2) irreplaceable: KeyPulse alone answers ≥5 questions and bash beats KeyPulse on none; (3) a Work-only grant returns no Personal record; (4) no-answer questions answered "not found" with no fabrication; (5) real DB upgrade with backup and integrity ok; (6) zero secrets/ID/bank-card hits and zero out-of-scope buckets in replayed tool output; (7) CI synthetic eval gate; (8) p95 query latency < 2s.

Criterion 2 was originally "of 12 capture/retention questions, KeyPulse ≥10 and bash ≤2". It failed (bash 5) because questions were mis-labelled as capture-only: Chrome keeps its own history, and some files were still on disk. The user chose to redefine it from the measured result; the report must state the original criterion, why it failed, and that the new one is post hoc.

Known boundary accepted for M1: content in AI-tool apps/sites is bucketed Work by rule (D26/D28), so personal topics discussed in an AI chat are visible to a Work-only grant. Revisit in M3 (content-level or manual rebucketing).


## Timeline

- time: 2026-10-12T14:41:55
  kind: decision
  summary: "Created this page: D29: M1 signs off on a real-data control eval (KeyPulse vs bash), with a measured 'irreplaceable' criterion"
  source: user 2026-10-12
  affects: [m1-real-data-acceptance]

- time: 2026-10-12T14:41:55
  kind: decision
  summary: "M1 acceptance = real-data control eval; criterion 2 redefined from measured results"
  source: user 2026-10-12
  affects: [m1-real-data-acceptance]

- time: 2026-10-12T17:27:27
  kind: evidence
  summary: "2026-10-12 auto-judged rerun (M2-0 harness, Sonnet judge, strict rubric): on the 19 A–C questions KP is 14/19 correct, not the 16/19 (84%) from human pre-labels; review found the pre-labels lenient on 2–3 items (judge more accurate on Q06/Q15-type). M2 baseline = auto-judged 14/19 (74%); bash 11/21. Single runs vary by ±2 questions from model nondeterminism, so M2 acceptance should use 3 repeated runs. Imported sources (codex/claude transcripts) are not email-redacted at query time (only secrets), unlike capture streams."
  affects: [m1-real-data-acceptance]
