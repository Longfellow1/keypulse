---
id: product-positioning-memory-infra
title: "D30: KeyPulse is user-controlled memory infrastructure shared by all AI assistants; hero scenario = verifiable recall"
category: decision
status: active
tags: [positioning, roadmap]
created: "2026-10-12T15:20:36"
updated: "2026-10-12T15:20:36"
---

<!-- compiled_truth -->
Positioning: KeyPulse persists the user's authorized digital trail and turns cross-app, cross-time traces into personal memory that any AI can call safely, with traceable sources. It is not another AI assistant and does not build its own chat entry point; the user keeps asking in Claude, Codex, Cursor, etc.

Three layers: background memory engine (capture, import, clean, bucket, dedup, evidence); menu-bar Memory Control Center (is it recording, which sources/sites/buckets are on, what each AI may see, who queried what, pause/delete/correct/rebucket); the user's existing AI as the entry point.

Hero scenario for V1: in any AI, find something the user once saw, said or wrote but no longer knows where it lives, with a verifiable source.

Guardrail: coverage is not the value. Trust (ranking, evidence deep links, scoped access, correction) and long-term retention are the moat. M1 evidence: all 3 misses came from ranking or capture precision, none from missing sources.

Milestones after M1 (user decision 2026-10-12):
- M2 = trustworthy recall AND the AX committed-text (Chinese) spike, both. Recall: ranking for near-duplicate/repeated events, evidence for all sources (#57), coverage status in tool output, lightweight result feedback, and a re-runnable real-data eval with auto-judging. Exit: answer accuracy ≥95%, evidence openable ≥95%, zero out-of-scope results.
- M3 = Memory Control Center (HUD), including content-level rebucketing/manual correction and per-AI access/audit view.
- M4 = release, gated by external validation: 10-15 heavy AI users on Mac, 14 days, ≥5 real recall questions each; the signal is unprompted return in week 2; any permission leak is a severe failure.

Not adopted yet: claims that KeyPulse "subsumes" Pieces/Memmy (unverified); agent write-back of long-term memory (V1 is read-only; revisit after M4); proactive machine discovery (after trust is established, via a user-confirmed candidate inbox).


## Timeline

- time: 2026-10-12T15:20:36
  kind: decision
  summary: "Created this page: D30: KeyPulse is user-controlled memory infrastructure shared by all AI assistants; hero scenario = verifiable recall"
  source: "user 2026-10-12 (third-party review by Codex, adopted with reservations)"
  affects: [product-positioning-memory-infra]

- time: 2026-10-12T15:20:36
  kind: decision
  summary: "Positioning, hero scenario, milestone order after M1"
  source: user 2026-10-12
  affects: [product-positioning-memory-infra]
