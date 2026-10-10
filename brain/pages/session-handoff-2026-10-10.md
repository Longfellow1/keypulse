---
id: session-handoff-2026-10-10
title: "Session handoff 2026-10-10: current state for a fresh session"
category: project
status: active
tags: [handoff, state, refactor]
created: "2026-10-10T11:19:23"
updated: "2026-10-10T11:30:23"
---

<!-- compiled_truth -->
## Repo state
- Branch: `main`. Tag `legacy-v0` = commit `66ddd2d` (frozen baseline). NOT pushed to GitHub yet.
- Local main is 1 commit ahead of `origin/main` (398574df).
- Uncommitted: `brain/` edits, `docs/extraction/` (new), PRD v1.2 revision (`docs/KeyPulse_V2.md`, being edited by another agent; do not touch).
- Refactor is in place in github.com/Longfellow1/keypulse. Planned branch `refactor/core-v1`.
- Planned contract: import-linter. The core package must not import pipeline / obsidian / hud / quality. Enforced in CI.

## Test baseline at legacy-v0 (HOME redirected to temp dir)
- 1243 tests: 10 failed, 1215 passed, 19 skipped.
- 8 failures come from the legacy-v0 rename commit. Tests hard-code old paths (`docs/golden-*`, `keypulse/prompts/daily_flagship.v2.md`). Also check production validator code for hard-coded old paths.
- 1 depends on real `~/.keypulse` data: `test_daily_flagship_context_helpers_read_real_may_9_history`.
- 1 suspected macOS / env: `test_capture_browser_url...unsupported_dialect...`.
- Plan: update references to the renamed paths, rerun, target only the 2 non-rename failures.
- Pointers: `docs/extraction/pytest-baseline.txt`.

## Pending ops (need owner OK)
1. Fix the 8 rename-related tests (Sonnet).
2. Push `main` and `legacy-v0`.
3. Branches:
   - Push-backup then keep: `install-homebrew` (bilingual CLI f2241f3, not on main); `refactor/cleaning-unify` (raw_events source plugin 768e3a0, not on main).
   - Push-backup then delete: worktree `worktree-principle-vertical-v1`, `wip-before-reset-2026-05-19` (superseded). Worktree `principle-vertical-v1` has 118 uncommitted changes: review first.
   - Delete local and origin: `install-homebrew-clean`, `refactor/daily-renderer-unification`.
   - Already merged, safe to delete: `feat/daily-v3-m5`, `feat/weekly-v3`, `fix/drop-keyboard-chunk`, `fix/p0-stability`.
   - `git worktree prune` for `/Users/Harland/Go/keypulse-homebrew` and `keypulse-install-clean`.
   - Four active worktrees under `.claude/worktrees/` have uncommitted changes (24 / 22 / 22 / 118). Do not delete before review.

## Housekeeping
- Parent `/Users/Harland/CLAUDE.md` describes another project (qi-agent) and loads here. Consider isolating.
- Stray scratch file `/Users/Harland/.keypulse_scratch_none` can be deleted (ask first).
- GitNexus MCP tools were not available this session, so `detect_changes` was not run for the freeze commit (rename and docs only).

## PRD v1.2 status
- B1-B8 and conflicts 1-5 are APPROVED. Details in [[v1-development-baseline-prd]]. No owner veto is pending.
- Still open: camera capture, Windows, personal-chat capture details, Cursor data-source format (unspecified in PRD, to confirm).

## Next steps (in order)
1. Owner writes the Eval PRD ([[eval-prd-separate]]). It MUST keep the "unauthorised-access leakage = 0" HARD release gate.
2. Commit the uncommitted work (`brain/`, `docs/extraction/`, `docs/KeyPulse_V2.md`, PRD V1 note). Owner OK needed before commit.
3. Fix the 8 rename-broken tests, push `main` + `legacy-v0`, branch cleanup. Owner OK needed per item.
4. P0 breakdown:
   - core package skeleton + import-linter rules;
   - data model per PRD §3.5;
   - MCP tools `search_context` / `get_timeline` / `get_evidence` + `commit_memory`, with token + allowlist auth ([[mcp-caller-auth-p0]], [[memory-distillation-by-agent]]);
   - extraction order (below).
5. Extraction order, after unblocking:
   - store, then search, then privacy / permissions (these need extension per PRD, not as-is), then capture / sources.
   - Blockers: `pipeline/__init__.py` eager imports; `sources/sink.py:11` imports `quality.entity_extractor`; quality cluster strategies import `obsidian.layout`; capture <-> capabilities cycle; `app.py` / `cli.py` hard-wired to pipeline + obsidian; DB schema ownership split (`store/migrations.py` v19 vs `pipeline/triggers.py` llm_trigger_log, `pipeline/run_record.py` run_records).
   - `hud` is NOT stay-legacy: PRD §3.9 makes it a P0 trust surface.
- Full blocker list: [[legacy-extraction-blockers]]. Refactor policy: [[refactor-in-place-with-legacy-tag]]. Decisions: [[keypulse-v1-local-memory-infra]], [[v1-development-baseline-prd]], [[multi-host-mcp-integration]], [[keyboard-capture-p0-experimental]].
- Structure and dependency analysis: `docs/extraction/structure-tree.md`, `docs/extraction/dependency-map.md`, scripts in `docs/extraction/scripts/`.


## Timeline

- time: 2026-10-10T11:19:23
  kind: decision
  summary: "Created this page: Session handoff 2026-10-10: current state for a fresh session"
  source: Session 2026-10-10 handoff
  affects: [session-handoff-2026-10-10]

- time: 2026-10-10T11:19:23
  kind: decision
  summary: "Capture repo, test, branch and pending-op state at handoff"
  source: Session 2026-10-10
  affects: [session-handoff-2026-10-10]

- time: 2026-10-10T11:30:23
  kind: decision
  summary: "Next steps rewritten: PRD v1.2 B1-B8 approved, no owner veto needed; order updated; open items added"
  source: "Owner delegated to assistant recommendation, 2026-10-10"
  affects: [session-handoff-2026-10-10]
