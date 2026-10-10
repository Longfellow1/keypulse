---
slug: architecture
title: System architecture
role: system architecture
updated: "2026-10-10T10:25:44"
---

# System architecture

## Layers
```mermaid
graph LR
  subgraph Capture
    W[capture/ watchers<br/>keyboard_chunk, browser_url, ...]
    SRC[sources/ plugins + discoverers<br/>git, claude_code, codex_cli, chrome/safari, spotlight, leveldb, markdown_vault]
  end
  PRIV[privacy/ desensitizer + detectors]
  DB[(~/.keypulse/keypulse.db<br/>SQLite)]
  PL[pipeline/ sessions, entities, daily + weekly orchestrators]
  GW[model gateway<br/>cloud / local profiles]
  QA[quality/ + daily_validator / weekly_validator]
  AN[(~/.keypulse/anchors.json<br/>anchor SSOT)]
  OBS[obsidian/ exporter<br/>vault: ~/Go/Knowledge]
  HUD[hud/ WKWebView menu bar]
  H[health/ self_heal, product_delivery<br/>capabilities/ registry + store]
  W --> PRIV
  SRC --> PRIV
  PRIV --> DB
  DB --> PL
  PL <--> GW
  PL --> QA
  PL --> AN
  AN --> OBS
  PL --> OBS
  PL --> HUD
  H -.watches.-> W
  H -.watches.-> PL
```

## Entry points
- `keypulse/cli.py` — single click group `main` (~4.4k lines). Subgroups: `daily`, `weekly`, `obsidian`, `pipeline`, `eval`, `install`, `hud`, `model`, `config`, `session`, `sinks`. Hidden/dev commands live under `dev`.
- `app_main.py` + `setup_app.py` — py2app bundle entry (double-click routes to `hud start`).
- `integrations/launchd/` — plists for daemon, healthcheck, obsidian-sync (hourly + on-trigger).
- `Makefile` — `bootstrap`, `app`, `preflight`, `install`, `onboard`, `reload`.

## Module map (`keypulse/`)
- `capture/` live watchers; `sources/` batch data-source plugins; `privacy/` desensitization.
- `pipeline/` ~50 modules: orchestrators, entity extract/merge, principle distillation (L7), validators.
- `prompts/` — LLM prompt files; `loader.py` matches a prompt by frontmatter `capability:`; JSON schemas in `prompts/schemas/`.
- `obsidian/` export layout and quality gate; `hud/`; `health/`; `capabilities/`; `store/`; `services/`; `observability/`; `search/`.

## Docs layout (gotcha)
- ~100 files under `docs/` and `keypulse/prompts/` were **renamed with Chinese prefixes but the rename is uncommitted** (old paths show as `D`, new paths as `??`). Current working-tree names: `docs/说明-*`, `docs/设计-*`, `docs/记录-*`, `docs/评测-*`, `keypulse/prompts/备忘-*`.
- `CLAUDE.md` and `AGENTS.md` still cite old paths (e.g. `docs/golden-daily/2026-05-06-v4.md`). Current name: `docs/golden-daily/评测-2026-05-06-v4.md`.
- Also: `/Users/Harland/CLAUDE.md` (parent dir) describes a different project (qi-agent / T16A, `core/` tests) and will be loaded for this repo too.
