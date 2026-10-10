---
slug: background
title: Project background
role: project background
updated: "2026-10-10T10:25:44"
---

# Project background

## What it is
KeyPulse: a local-first macOS tool (Python package `keypulse` 0.1.0, CLI `keypulse`). It captures work traces (window/keyboard activity, browser history, git, Claude/Codex sessions, markdown vaults), desensitizes them, stores them in SQLite, and turns them into **daily and weekly narratives** exported to an Obsidian vault and a menu-bar HUD.

## Goal (as stated in repo)
- Turn real work output into an explainable narrative record ("产出叙事"), per `docs/EVOLUTION.md` header and README.
- Product stage per 2026-05-15 timeline: "能装能信能日用" (installable, trustworthy, usable daily). Source: `docs/说明-EVOLUTION.md`.
- Positioning/strategy docs: `docs/说明-positioning.md`, `docs/说明-phase2-product-strategy.md` (not re-read in this pass).

## Standing product principles
- **User names/curates, LLM only proposes, program writes core state.** (project brief; applies to the planned skill layer, see [[user-curates-llm-proposes]]).
- Generic over per-app special cases: "install a new app with zero code" (2026-05-15 timeline entry).
- Local-first: data stays under `~/.keypulse/`; sinks export only after privacy filtering.

## Users and non-goals
- Target users: **low-confidence / TODO confirm** — inferred as macOS power users who want a personal activity memory. Not verified against any user doc.
- Non-goals: **TODO confirm.** Known negatives only: no screen-recording/OCR path (retired 2026-05-14, see [[db-direct-read-over-screen-capture]]); no Windows/Linux support is implied by the code (macOS-specific pyobjc deps).
