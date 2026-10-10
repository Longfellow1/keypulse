---
id: db-direct-read-over-screen-capture
title: "Read local databases directly; screen capture is not the main path"
category: decision
status: active
created: "2026-10-10T10:26:12"
updated: "2026-10-10T10:26:12"
---

<!-- compiled_truth -->
## Decision
Primary data path is direct reads of local databases and metadata (browser history, KnowledgeC, LevelDB, Spotlight, git, agent session files). Screen-based capture is a downgrade, not the core.

## Alternatives
- Screen OCR watcher: built, then retired. Commit d4eee33 (2026-05-15) took it offline ("日均 9 条 / 权重 0.5 / 屏幕录制权限门槛高"); code kept commented for rollback.
- Keyboard input instead: `keyboard_chunk` watcher (Quartz CGEventTap, silence-based chunking) replaced it.

## Rationale
Low event volume and a high Screen Recording permission barrier made OCR poor value per permission prompt.

## Blast radius
`keypulse/sources/` plugins and discoverers; `keypulse/capture/` watchers; macOS permission onboarding.

## Open
Whether OCR should come back for specific apps: unknown, not discussed in code.


## Timeline

- time: 2026-10-10T10:26:12
  kind: decision
  summary: "Created this page: Read local databases directly; screen capture is not the main path"
  source: "docs/说明-EVOLUTION.md; docs/设计-data-source-architecture.md §1; commit d4eee33"
  affects: [db-direct-read-over-screen-capture]

- time: 2026-10-10T10:26:12
  kind: decision
  summary: captured from project history
  source: "docs/说明-EVOLUTION.md; docs/设计-data-source-architecture.md §1; commit d4eee33"
  affects: [db-direct-read-over-screen-capture]
