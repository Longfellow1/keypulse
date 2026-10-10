---
slug: stack
title: Tech stack
role: tech-stack choices
updated: "2026-10-10T10:25:44"
---

# Tech stack

| Domain | Choice | Where / why |
|---|---|---|
| Language | Python >=3.11 | `pyproject.toml` |
| CLI | click >=8.1, rich >=13.7 | `keypulse/cli.py` |
| Config/models | pydantic v2, pydantic-settings, pyyaml | `keypulse/config.py`, schemas |
| Storage | SQLite `~/.keypulse/keypulse.db` | `keypulse/utils/paths.py`, `config.py` |
| Canonical anchors | `~/.keypulse/anchors.json` | `pipeline/weekly_topic_anchor.py` |
| macOS native | pyobjc (Cocoa, WebKit, ApplicationServices, Quartz, Vision) — optional `[macos]` extra | HUD (WKWebView), keyboard CGEventTap, AX |
| App bundle | py2app (`[build]` extra), `setup_app.py`, `scripts/preflight_app.py` | `make app` / `make preflight` |
| Services | launchd LaunchAgents | `integrations/launchd/` |
| LLM | model gateway with cloud/local profiles + circuit breaker | `keypulse/pipeline/model.py`, `keypulse model setup` |
| Output | Obsidian markdown vault (`~/Go/Knowledge`, per brief) + HUD | `keypulse/obsidian/` |
| Tests | pytest under `tests/` (~170 entries) | `README.md` claims 800+ passing; EVOLUTION 2026-05-15 says 1056 passed. **Current count: unverified.** |
| Lint | ruff (`.ruff_cache` present) | config not re-verified |
| Env | `.venv` created by `make bootstrap` | `Makefile` hardcodes `/Users/Harland/Go/keypulse/.venv` |

Gotcha: Makefile hardcodes absolute paths (`ROOT`, `PYTHON`), so a clone elsewhere needs edits.
