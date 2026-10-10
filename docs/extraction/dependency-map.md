# KeyPulse dependency map

Repo: `/Users/Harland/Go/keypulse` (read-only). Analysis script: `scratchpad/analyze.py` (python3 -I, `ast`, static). Raw output: `scratchpad/analysis.json`.

## Method

- Scope: the 205 `.py` files under `keypulse/`. `tests/`, `scripts/` and root scripts are not in the graph.
- Unit = subpackage directory (`capture`, `pipeline`, ...). Root modules are `root:<name>` (`root:cli` = `keypulse/cli.py`). `root:__init__` = `keypulse/__init__.py`.
- Each `import`/`from ... import` statement counted once per (source unit, target unit) pair. Relative imports resolved. Self-edges dropped.
- `module-level` = the import is a top-level statement; the rest are lazy (inside functions/methods). Lazy imports are included in the main count.
- Static only: `importlib`/string-based imports are invisible. Third-party = top-level module not in `sys.stdlib_module_names` and not `keypulse`.

## 1. Internal adjacency (source -> target: total imports / module-level)

| Source | Target | Total | Module-level |
|---|---|---:|---:|
| `pipeline/` | `utils/` | 16 | 16 |
| `cli.py` | `pipeline/` | 12 | 8 |
| `capture/` | `store/` | 11 | 11 |
| `capture/` | `utils/` | 11 | 11 |
| `pipeline/` | `prompts/` | 10 | 7 |
| `pipeline/` | `config.py` | 9 | 7 |
| `capabilities/` | `store/` | 7 | 5 |
| `health/` | `utils/` | 7 | 7 |
| `cli.py` | `store/` | 7 | 3 |
| `cli.py` | `utils/` | 7 | 6 |
| `health/` | `store/` | 6 | 5 |
| `health/` | `capabilities/` | 5 | 5 |
| `hud/` | `store/` | 5 | 4 |
| `pipeline/` | `store/` | 5 | 5 |
| `services/` | `store/` | 5 | 5 |
| `sources/` | `utils/` | 5 | 5 |
| `hud/` | `capabilities/` | 4 | 4 |
| `hud/` | `utils/` | 4 | 4 |
| `obsidian/` | `utils/` | 4 | 4 |
| `pipeline/` | `obsidian/` | 4 | 4 |
| `app.py` | `capabilities/` | 4 | 4 |
| `app.py` | `pipeline/` | 4 | 1 |
| `app.py` | `utils/` | 4 | 2 |
| `cli.py` | `services/` | 4 | 4 |
| `sources/` | `store/` | 4 | 4 |
| `hud/` | `health/` | 3 | 2 |
| `hud/` | `config.py` | 3 | 3 |
| `hud/` | `sources/` | 3 | 3 |
| `obsidian/` | `pipeline/` | 3 | 2 |
| `pipeline/` | `i18n.py` | 3 | 3 |
| `pipeline/` | `sources/` | 3 | 3 |
| `sources/` | `privacy/` | 3 | 3 |
| `capabilities/` | `config.py` | 2 | 2 |
| `health/` | `app.py` | 2 | 1 |
| `hud/` | `pipeline/` | 2 | 1 |
| `obsidian/` | `quality/` | 2 | 2 |
| `obsidian/` | `store/` | 2 | 1 |
| `pipeline/` | `integrations/` | 2 | 2 |
| `quality/` | `obsidian/` | 2 | 2 |
| `app.py` | `capture/` | 2 | 0 |
| `app.py` | `store/` | 2 | 1 |
| `cli.py` | `health/` | 2 | 0 |
| `cli.py` | `obsidian/` | 2 | 2 |
| `cli.py` | `privacy/` | 2 | 1 |
| `cli.py` | `app.py` | 2 | 1 |
| `cli.py` | `search/` | 2 | 2 |
| `search/` | `store/` | 2 | 2 |
| `services/` | `utils/` | 2 | 2 |
| `capabilities/` | `capture/` | 1 | 0 |
| `capture/` | `capabilities/` | 1 | 1 |
| `capture/` | `privacy/` | 1 | 1 |
| `capture/` | `config.py` | 1 | 1 |
| `health/` | `observability/` | 1 | 1 |
| `health/` | `pipeline/` | 1 | 1 |
| `health/` | `config.py` | 1 | 1 |
| `hud/` | `capture/` | 1 | 1 |
| `hud/` | `app.py` | 1 | 1 |
| `integrations/` | `config.py` | 1 | 1 |
| `obsidian/` | `i18n.py` | 1 | 1 |
| `pipeline/` | `hud/` | 1 | 1 |
| `pipeline/` | `observability/` | 1 | 1 |
| `quality/` | `pipeline/` | 1 | 0 |
| `quality/` | `sources/` | 1 | 0 |
| `app.py` | `integrations/` | 1 | 0 |
| `app.py` | `config.py` | 1 | 1 |
| `app.py` | `permissions.py` | 1 | 0 |
| `app.py` | `services/` | 1 | 0 |
| `app.py` | `sources/` | 1 | 1 |
| `cli.py` | `capture/` | 1 | 1 |
| `cli.py` | `hud/` | 1 | 1 |
| `cli.py` | `integrations/` | 1 | 1 |
| `cli.py` | `config.py` | 1 | 1 |
| `cli.py` | `i18n.py` | 1 | 1 |
| `cli.py` | `permissions.py` | 1 | 0 |
| `cli.py` | `sources/` | 1 | 1 |
| `config.py` | `utils/` | 1 | 0 |
| `permissions.py` | `utils/` | 1 | 1 |
| `services/` | `obsidian/` | 1 | 1 |
| `sources/` | `quality/` | 1 | 1 |
| `sources/` | `config.py` | 1 | 1 |
| `utils/` | `config.py` | 1 | 0 |

Total internal import statements: 250 (206 module-level).

## 2. Fan-in / fan-out per unit

| Unit | Imports out (stmts) | Distinct targets | Imports in (stmts) | Distinct sources |
|---|---:|---:|---:|---:|
| `pipeline/` | 54 | 10 | 23 | 6 |
| `utils/` | 1 | 1 | 62 | 11 |
| `store/` | 0 | 0 | 56 | 11 |
| `cli.py` | 47 | 16 | 0 | 0 |
| `capture/` | 25 | 5 | 5 | 4 |
| `health/` | 23 | 7 | 5 | 2 |
| `hud/` | 26 | 9 | 2 | 2 |
| `app.py` | 21 | 10 | 5 | 3 |
| `capabilities/` | 10 | 3 | 14 | 4 |
| `sources/` | 14 | 5 | 9 | 5 |
| `config.py` | 1 | 1 | 21 | 10 |
| `obsidian/` | 12 | 5 | 9 | 4 |
| `services/` | 8 | 3 | 5 | 2 |
| `prompts/` | 0 | 0 | 10 | 1 |
| `quality/` | 4 | 3 | 3 | 2 |
| `privacy/` | 0 | 0 | 6 | 3 |
| `integrations/` | 1 | 1 | 4 | 3 |
| `i18n.py` | 0 | 0 | 5 | 3 |
| `search/` | 2 | 1 | 2 | 1 |
| `permissions.py` | 1 | 1 | 2 | 2 |
| `observability/` | 0 | 0 | 2 | 2 |

## 3. Cycles

Strongly connected components (Tarjan) on the unit graph.

- All imports (incl. lazy): {config.py, utils/}; {capabilities/, capture/}; {health/, hud/, obsidian/, pipeline/, quality/, app.py, services/, sources/}
- Module-level imports only: {health/, hud/, obsidian/, pipeline/, quality/, app.py, sources/}
- File-level: no file-level cycle found among resolved files.
- Concrete 2-cycles: `config <-> utils` = `keypulse/utils/dates.py:30` (lazy `from keypulse.config import Config`) and `keypulse/config.py:296` (lazy `from keypulse.utils.env import load_dotenv_quiet`). `capabilities <-> capture` = `capture/watchers/browser_url.py:7` and `capabilities/builtin/browser_automation.py:169` (lazy).
- Large SCC paths (checked against the edge table): `pipeline <-> obsidian` (pipeline->obsidian 4, obsidian->pipeline 3); `quality <-> obsidian` (2 each way); `pipeline -> hud -> sources -> quality -> pipeline`; `hud -> health -> root:app -> pipeline -> hud`; `root:app -> services -> obsidian`, which closes the loop through `pipeline`.

## 4. Third-party imports per unit

Import statements by top-level module (from `ast`, includes lazy imports).

| Unit | Third-party modules (count) |
|---|---|
| `capabilities/` | ApplicationServices (1), AppKit (1), Quartz (1) |
| `capture/` | AppKit (8), Quartz (4), ApplicationServices (3), objc (1) |
| `cli.py` | rich (4), Quartz (3), click (1), yaml (1), AppKit (1), Vision (1), ApplicationServices (1) |
| `config.py` | pydantic (1) |
| `hud/` | AppKit (2), objc (2), Foundation (1), WebKit (1) |
| `permissions.py` | Quartz (4), ApplicationServices (2), CoreFoundation (1), objc (1) |
| `pipeline/` | yaml (1), pydantic (1) |
| `sources/` | click (1), objc (1), AppKit (1), Foundation (1) |

No third-party imports (stdlib + keypulse only): `store`, `utils`, `privacy`, `search`, `services`, `obsidian`, `quality`, `health`, `prompts`, `integrations`, `observability`, `root:app`, `root:i18n`.

## 5. pyobjc / Cocoa-binding files

**17 files under `keypulse/`** import a pyobjc-family module (AppKit, ApplicationServices, Quartz, objc, Foundation, Vision, WebKit, CoreFoundation), with 1 more in `tests/test_browser_discovery.py`. This is **17, not the ~25 quoted**. Not reconciled with the earlier figure.

| File | Line | Module |
|---|---:|---|
| `keypulse/capabilities/builtin/accessibility_permission.py` | 36 | ApplicationServices |
| `keypulse/capabilities/builtin/appkit_runtime.py` | 17 | AppKit |
| `keypulse/capabilities/builtin/screen_recording_permission.py` | 37 | Quartz |
| `keypulse/capture/active_app.py` | 27 | AppKit |
| `keypulse/capture/active_app.py` | 47 | AppKit |
| `keypulse/capture/watchers/ax_text.py` | 81 | AppKit |
| `keypulse/capture/watchers/ax_text.py` | 85 | ApplicationServices |
| `keypulse/capture/watchers/browser.py` | 26 | AppKit |
| `keypulse/capture/watchers/browser_discovery.py` | 9 | AppKit |
| `keypulse/capture/watchers/browser_discovery.py` | 10 | objc |
| `keypulse/capture/watchers/clipboard.py` | 33 | AppKit |
| `keypulse/capture/watchers/idle.py` | 19 | Quartz |
| `keypulse/capture/watchers/keyboard_native.py` | 23 | Quartz |
| `keypulse/capture/watchers/keyboard_native.py` | 33 | ApplicationServices |
| `keypulse/capture/watchers/keyboard_native.py` | 54 | Quartz |
| `keypulse/capture/watchers/window.py` | 40 | AppKit |
| `keypulse/capture/watchers/window.py` | 62 | ApplicationServices |
| `keypulse/capture/watchers/window_capture.py` | 8 | AppKit |
| `keypulse/capture/watchers/window_capture.py` | 12 | Quartz |
| `keypulse/cli.py` | 1213 | AppKit |
| `keypulse/cli.py` | 1220 | Quartz |
| `keypulse/cli.py` | 1226 | Vision |
| `keypulse/cli.py` | 1233 | ApplicationServices |
| `keypulse/cli.py` | 1239 | Quartz |
| `keypulse/cli.py` | 1246 | Quartz |
| `keypulse/hud/app.py` | 11 | AppKit |
| `keypulse/hud/app.py` | 12 | objc |
| `keypulse/hud/app.py` | 13 | Foundation |
| `keypulse/hud/app.py` | 14 | WebKit |
| `keypulse/hud/approval_window.py` | 8 | AppKit |
| `keypulse/hud/approval_window.py` | 13 | objc |
| `keypulse/permissions.py` | 41 | ApplicationServices |
| `keypulse/permissions.py` | 49 | ApplicationServices |
| `keypulse/permissions.py` | 50 | CoreFoundation |
| `keypulse/permissions.py` | 51 | objc |
| `keypulse/permissions.py` | 66 | Quartz |
| `keypulse/permissions.py` | 77 | Quartz |
| `keypulse/permissions.py` | 89 | Quartz |
| `keypulse/permissions.py` | 100 | Quartz |
| `keypulse/sources/scheduler.py` | 84 | objc |
| `keypulse/sources/scheduler.py` | 85 | AppKit |
| `keypulse/sources/scheduler.py` | 92 | Foundation |

## 6. Top coupling blockers (file:line verified)

1. **pipeline/__init__.py:1-43 eager imports.** Lines 1-15 import aggregate, contracts, feedback, model, record, themes, surface, policy at package import time, so importing any `keypulse.pipeline.*` submodule loads the whole pipeline. Consequence: `pipeline` is effectively unmovable without a lazy `__init__`.
2. **sources -> quality; quality -> obsidian.** `keypulse/sources/sink.py:11` imports `keypulse.quality.entity_extractor`. `keypulse/quality/strategies/cluster/s003_reject_url_like.py:1` and `s004_reject_file_path.py:1` import `keypulse.obsidian.layout.slugify`. The quality framework pulls the Obsidian layout module in.
3. **capture <-> capabilities.** `keypulse/capture/watchers/browser_url.py:7` imports `keypulse.capabilities.builtin.browser_automation` at module top. The back-edge is `capabilities/builtin/browser_automation.py:169` (lazy import of `capture.watchers.browser_discovery`, which pulls AppKit). `keypulse/capabilities/builtin/__init__.py:20-32` imports the builtin capabilities (line 33 commented out), and `:68` `register_builtin_capabilities()` registers them all, so importing any builtin loads the whole set.
4. **app.py and cli.py hard-wired to pipeline/obsidian.** `app.py:20` imports `pipeline.daily_orchestrator.run_daily_after_obsidian_sync`; `app.py:156` imports `pipeline.load_model_gateway`; `app.py:276` and `app.py:305` import `pipeline.triggers`. `cli.py:52-85` imports triggers (52), services (53), search (54), capture (55), utils (56), hud (57), pipeline package (58-64), model (66), i18n (67), model_keychain (68), daily_orchestrator (76), daily_summary (77), onboarding (78), weekly_orchestrator (85).
5. **DB schema ownership split.** `keypulse/store/migrations.py:4` `SCHEMA_VERSION = 19`. Legacy tables still created there: `profile_identity` (:137), `profile_entities` (:140), `profile_projects` (:143), `hourly_summaries` (:149), `daily_skeletons` (:159). Tables created outside the store: `keypulse/pipeline/triggers.py:49` `llm_trigger_log` (plus index at :60) and `keypulse/pipeline/run_record.py:236` `run_records` (plus index at :251).
6. **hud <-> pipeline and health -> pipeline.** `keypulse/health/self_heal.py:17` imports `pipeline.daily_orchestrator.run_daily`. `hud -> pipeline` (`hud/summary.py:19` and `:305`, `pipeline.surface`) and `pipeline -> hud` (`pipeline/weekly_orchestrator.py:20`, `hud.state`) form a 2-cycle at unit level.

## 7. Classification (copied from earlier scan) with checked evidence

Classification copied as given. Evidence column = facts checked in this pass, from the AST data and file reads.

| Class | Item | Outgoing internal (stmts) | Incoming internal (stmts) | Evidence / note |
|---|---|---|---|---|
| EXTRACT-AS-IS | search/ | store/(2) | cli.py(2) | Outgoing: store (2). Incoming: root:cli (2). Clean dependency on store only. |
| EXTRACT-AS-IS | privacy/ | none | capture/(1), cli.py(2), sources/(3) | Leaf: no internal imports. |
| EXTRACT-AS-IS | permissions.py | utils/(1) | app.py(1), cli.py(1) | Outgoing: utils (1). Incoming: root:app, root:cli. Imports pyobjc (ApplicationServices, Quartz, CoreFoundation, objc). |
| EXTRACT-WITH-REFACTOR | store/ | none | capabilities/(7), capture/(11), health/(6), hud/(5), obsidian/(2), pipeline/(5), app.py(2), cli.py(7), search/(2), services/(5), sources/(4) | Leaf for internal imports. Holds legacy profile_*/hourly_summaries/daily_skeletons (migrations.py:137-159), SCHEMA_VERSION 19 (migrations.py:4). llm_trigger_log and run_records are created outside store (see blocker 5). |
| EXTRACT-WITH-REFACTOR | capture/ | capabilities/(1), privacy/(1), config.py(1), store/(11), utils/(11) | capabilities/(1), hud/(1), app.py(2), cli.py(1) | Imports capabilities (1), privacy, utils, store, root:config. Imports pyobjc in 9 files (AppKit, Quartz, ApplicationServices, objc). |
| EXTRACT-WITH-REFACTOR | sources/ | privacy/(3), quality/(1), config.py(1), store/(4), utils/(5) | hud/(3), pipeline/(3), quality/(1), app.py(1), cli.py(1) | sink.py:11 imports keypulse.quality.entity_extractor (verified). |
| EXTRACT-WITH-REFACTOR | config.py + utils/ | utils/(1) | capabilities/(2), capture/(1), health/(1), hud/(3), integrations/(1), pipeline/(9), app.py(1), cli.py(1), sources/(1), utils/(1) | Cycle: utils/dates.py:30 <-> config.py:296 (lazy). |
| EXTRACT-WITH-REFACTOR | services/ | obsidian/(1), store/(5), utils/(2) | app.py(1), cli.py(4) | services -> obsidian (1), store (5), utils (2). Obsidian coupling is the item to check; export.py itself was not read in this pass. |
| UNCLEAR | quality/ | obsidian/(2), pipeline/(1), sources/(1) | obsidian/(2), sources/(1) | Imports obsidian (2), pipeline (1), sources (1). Blocker 2 (s003/s004 -> obsidian.layout) verified. |
| UNCLEAR | health/ | capabilities/(5), observability/(1), pipeline/(1), app.py(2), config.py(1), store/(6), utils/(7) | hud/(3), cli.py(2) | self_heal.py:17 imports pipeline.daily_orchestrator (verified). Imports capabilities (5), store (6), utils (7). |
| UNCLEAR | pipeline/model.py | n/a (package-level) | n/a | Not split out in this scan (package-level only). Model gateway is imported by app.py:156 and pipeline/__init__.py:11. |
| UNCLEAR | model_keychain.py | n/a (package-level) | n/a | Not a root file; lives in pipeline/. Imported by cli.py:68. Not analysed further. |
| UNCLEAR | triggers.py | n/a (package-level) | n/a | Lives at pipeline/triggers.py (not root). Owns llm_trigger_log (triggers.py:49). Imported by app.py:276,305 and cli.py:52. |
| UNCLEAR | capabilities/builtin/llm_backend.py | capture/(1), config.py(2), store/(7) | capture/(1), health/(5), hud/(4), app.py(4) | Not analysed in this pass beyond the package-level edges (capabilities -> root:config (2), store (7)). |
| STAY-IN-LEGACY | pipeline/ (rest) | hud/(1), integrations/(2), observability/(1), obsidian/(4), prompts/(10), config.py(9), i18n.py(3), sources/(3), store/(5), utils/(16) | health/(1), hud/(2), obsidian/(3), quality/(1), app.py(4), cli.py(12) | pipeline imports 10 packages; biggest fan-out: utils (16), prompts (10), root:config (9). Imported by root:cli (12), root:app (4), health, hud, obsidian, quality. |
| STAY-IN-LEGACY | obsidian/ | pipeline/(3), quality/(2), i18n.py(1), store/(2), utils/(4) | pipeline/(4), quality/(2), cli.py(2), services/(1) | Imports pipeline (3), quality (2), store, utils, root:i18n. |
| STAY-IN-LEGACY | hud/ | capabilities/(4), capture/(1), health/(3), pipeline/(2), app.py(1), config.py(3), sources/(3), store/(5), utils/(4) | pipeline/(1), cli.py(1) | Pyobjc: AppKit, Foundation, WebKit, objc (hud/app.py, hud/approval_window.py). Imports capabilities, capture, health, pipeline, sources, store. |
| STAY-IN-LEGACY | cli.py | capture/(1), health/(2), hud/(1), integrations/(1), obsidian/(2), pipeline/(12), privacy/(2), app.py(2), config.py(1), i18n.py(1), permissions.py(1), search/(2), services/(4), sources/(1), store/(7), utils/(7) | none | Wires nearly everything (see blocker 4). Imports pyobjc in cli.py (AppKit, Quartz, ApplicationServices, Vision). |
| STAY-IN-LEGACY | app.py | capabilities/(4), capture/(2), integrations/(1), pipeline/(4), config.py(1), permissions.py(1), services/(1), sources/(1), store/(2), utils/(4) | health/(2), hud/(1), cli.py(2) | Hard-wired to pipeline triggers and orchestrators (blocker 4). |
| STAY-IN-LEGACY | prompts/ | none | pipeline/(10) | Leaf for internal imports; consumed by pipeline (10). |
| STAY-IN-LEGACY | i18n.py | none | obsidian/(1), pipeline/(3), cli.py(1) | Used by root:cli, obsidian, pipeline. |
| STAY-IN-LEGACY | integrations/ | config.py(1) | pipeline/(2), app.py(1), cli.py(1) | Outgoing: root:config (1). Consumed by pipeline (2), root:app (1), root:cli (1). |
| STAY-IN-LEGACY | observability/ | none | health/(1), pipeline/(1) | Leaf for internal imports; consumed by health, pipeline. |

Note: `pipeline/model.py`, `model_keychain.py` and `triggers.py` are all under `keypulse/pipeline/` (the earlier list gave no prefix). Their rows show package-level edges only.

## 8. Direct connections to keypulse.db

Default path: `keypulse/config.py:11` `db_path = "~/.keypulse/keypulse.db"`; resolved by `keypulse/utils/paths.py:11` (`get_data_dir() / "keypulse.db"`). Canonical connection factory: `keypulse/store/db.py:26`. Entries marked `db_path` use a `db_path` variable; its origin was not traced per site.

| File:line | Context |
|---|---|
| `keypulse/store/db.py:26` | canonical `_get_conn()` (store factory) |
| `keypulse/app.py:215` | sqlite3.connect(db_path) |
| `keypulse/pipeline/signals.py:116` | db_path parameter |
| `keypulse/pipeline/run_record.py:232` | self.db_path (run_records) |
| `keypulse/pipeline/triggers.py:82,114,141,168,197` | db_path (llm_trigger_log), 5 call sites |
| `keypulse/pipeline/daily_orchestrator.py:542` | db_path parameter |
| `keypulse/pipeline/evidence.py:58,176,194` | db_path parameter, 3 call sites |
| `keypulse/capabilities/builtin/obsidian_sync_freshness.py:41` | db_path parameter (not traced) |
| `keypulse/obsidian/exporter.py:178` | db_path; exporter.py:120 returns `_keypulse_home() / "keypulse.db"` |

Not keypulse.db (external sources, mostly read-only `mode=ro` URIs): `sources/plugins/chrome_history.py:77`, `safari_history.py:64`, `approved_sqlite.py:145,237,500`, `knowledgec.py:115`; `sources/discoverers/sqlite.py:158,181,206` connects to discovered sqlite files with `uri=False` (not read-only).

## 9. Not verified / caveats

- Pyobjc count: 17 under `keypulse/` (AST), not ~25.
- Classification items for `services/export.py`, `capabilities/builtin/llm_backend.py`, `pipeline/model.py`, `model_keychain.py` are classification-only; file bodies were not read in this pass.
- Per-site origin of `db_path` (section 8) not traced except the ones cited.
- Static analysis misses dynamic imports and any `importlib` loading; `capabilities/builtin/__init__.py` registration is by explicit import, so it is covered.
