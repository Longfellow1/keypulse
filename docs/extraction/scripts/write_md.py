import json, os, collections
SP = "/private/tmp/claude-502/-Users-Harland-Go-keypulse/c7664793-02ba-4616-81c2-5db7c60940bf/scratchpad"
OUT = "/private/tmp/claude-502/-Users-Harland-Go-keypulse/c7664793-02ba-4616-81c2-5db7c60940bf/scratchpad"
T = json.load(open(f"{SP}/tree-data.json"))
A = json.load(open(f"{SP}/analysis.json"))
REPO = "/Users/Harland/Go/keypulse"
ROW = "| {} |"

# ---------------- structure-tree.md ----------------
L = []
L.append("# KeyPulse project structure tree\n")
L.append("Source: `/Users/Harland/Go/keypulse` (read-only). LOC = `wc -l`. Totals count `.py` only unless noted.")
L.append("Excluded: .git, .venv, build, dist, __pycache__, .claude, .codex-home, .sandbox-home, node_modules, *.log, .keypulse (runtime data), plus .DS_Store, keypulse.egg-info, .gitnexus, .pytest_cache, .ruff_cache (tooling caches, not project source).\n")
L.append("## 0. Top level\n")
L.append("| Entry | Content |")
L.append("|---|---|")
L.append("| `keypulse/` | Python package, 205 `.py` files, 40,879 LOC (plus 29 md, 28 json, 1 yaml non-py files) |")
L.append("| `tests/` | 164 `.py` files, 26,025 LOC, flat, plus `fixtures/` and `golden/` |")
L.append("| `scripts/` | 12 `.py`, 1 `.sh`, 2 launchd plists (one-off migrations, evals, preflight) |")
L.append("| `docs/` | 40 top-level files + 56 files in 10 subdirs (design, eval, prompt-candidate and review notes) |")
L.append("| `brain/` | Project brain: 7 root pages + `pages/` (11 decision pages) |")
L.append("| `integrations/` | launchd plists (3) and the Obsidian vault bridge (README, 3 templates, layout note) |")
L.append("| root files | `app_main.py`, `setup_app.py`, `pyproject.toml`, `Makefile`, `install.sh`, `config.toml`, `README*.md`, `CLAUDE.md`, `AGENTS.md`, `BRAIN.md`, `CHANGELOG.md`, `TESTING.md`, `SUMMARY.md`, several `备忘-*.md` design notes, `config.toml.bak`/`.bak2` |")
L.append("")
L.append("## 1. keypulse/ package (file-level tree)\n")
L.append("Format: `name  LOC  one-line purpose`. Purpose = first non-empty line of the module docstring (`(no docstring)` if none). Non-py files show their first heading line (md) or type.\n")
L.append("```")
L += T["tree_lines"]
L.append("```\n")
L.append("### 1a. Per-subpackage totals (.py only)\n")
L.append("| Subpackage | .py files | LOC |")
L.append("|---|---:|---:|")
tot_f = tot_l = 0
for k, v in sorted(T["rows_by_sub"].items(), key=lambda x: -x[1]["loc"]):
    L.append(f"| `{k}/` | {v['py']} | {v['loc']:,} |" if k != "(root)" else f"| `keypulse/` root files | {v['py']} | {v['loc']:,} |")
    tot_f += v["py"]; tot_l += v["loc"]
L.append(f"| **total** | **{tot_f}** | **{tot_l:,}** |\n")

L.append("## 2. tests/ (flat, 164 .py files)\n")
L.append("Grouped by the keypulse subpackage each test imports most often (`keypulse.<unit>` import count per file; `root:<x>` = root module `keypulse/<x>.py`). Counts include `conftest.py`.\n")
L.append("| Tested subpackage | Test files | LOC |")
L.append("|---|---:|---:|")
for g, (n, loc, _) in sorted(T["groups"].items(), key=lambda x: -x[1][0]):
    L.append(f"| `{g}` | {n} | {loc:,} |")
L.append(f"| **total** | **{T['test_py_total']}** | **{T['test_loc_total']:,}** |\n")
L.append("`fixtures/` (package, not a test module):\n")
for p, n in T["fixtures"]:
    L.append(f"- `{p}` ({n} LOC)")
L.append("\n`golden/`:\n")
for p, n in T["golden"]:
    L.append(f"- `{p}` ({n} LOC)")
L.append("")

L.append("## 3. scripts/\n")
L.append("| File | LOC | Purpose |")
L.append("|---|---:|---|")
hand = {"smoke_py2app.sh": "Smoke test: runs the built `/Applications/KeyPulse.app` binary (py2app bundle)",
        "com.keypulse.daily.plist": "launchd job wrapping a `zsh -lc` daily run",
        "com.keypulse.weekly.plist": "launchd job wrapping a `zsh -lc` weekly run"}
for f, n, p in T["scripts"]:
    L.append(f"| `{f}` | {n} | {hand.get(f, p)} |")
L.append("")

L.append("## 4. docs/ (by subdir, recursive file counts)\n")
L.append("| Subdir | Files | Purpose (from file names) |")
L.append("|---|---:|---|")
dp = {"analysis": "Endgame / distillation strategy analysis",
      "daily-prompt-candidates": "Daily prompt candidates A/B/C write-up (earlier round)",
      "daily-prompt-candidates-v4": "Daily flagship v4 candidate runs (B2-B5 x runs) + meta.json",
      "golden-daily": "Daily Gold Set baselines (2026-05-06 v3/v4, 2026-05-09) and schema",
      "golden-weekly": "Weekly Gold Set (W19 exec/plain, W22 postfix) and golden spec",
      "images": "HUD v1 screenshot",
      "pk-weekly-w19": "W19 weekly outputs from three model backends (deepseek, doubao, ernie)",
      "reviews": "External review rounds 2026-04-25 and 2026-04-30 (+ judgment notes)",
      "superpowers": "Design spec(s) under superpowers/specs",
      "weekly-prompt-candidates-v3": "Weekly v3 candidates a/b/c, run1 exec/plain + meta.json"}
for d, n in T["docs_sub"]:
    L.append(f"| `docs/{d}/` | {n} | {dp.get(d, '')} |")
L.append(f"| `docs/` (top level) | {T['docs_top']} | Design notes, PRD (`KeyPulse_V1_PRD.md`), HUD mockups, eval JSON/MD, 说明/设计/记录 notes |")
L.append("")

L.append("## 5. brain/\n")
L.append("| Path | Purpose |")
L.append("|---|---|")
L.append("| `brain/index.md` | Brain index (catalog of root pages and pages/) |")
L.append("| `brain/architecture.md` | System architecture |")
L.append("| `brain/background.md` | Project background |")
L.append("| `brain/flow.md` | Key flows |")
L.append("| `brain/mindmap.md` | Feature mindmap |")
L.append("| `brain/roadmap.md` | Roadmap |")
L.append("| `brain/stack.md` | Tech stack |")
L.append("| `brain/pages/` (11) | Decision pages: anchors.json as SSOT, beat vs emit separation, capability fact vs probe, daily single render entry, DB-direct read over screen capture, Gold Set gated prompt changes, knowledge-graph layer (partial), py2app preflight smoke test, skill layer (planned), TCC refresh on install, user curates / LLM proposes |\n")

L.append("## 6. integrations/\n")
L.append("| Path | Purpose |")
L.append("|---|---|")
L.append("| `integrations/launchd/` (3 plists) | `com.keypulse.healthcheck` (StartInterval, runs `KeyPulse healthcheck`); `com.keypulse.obsidian-sync-hourly` (StartInterval, `keypulse obsidian`); `com.keypulse.obsidian-sync` (StartCalendarInterval, `keypulse obsidian`) |")
L.append("| `integrations/obsidian/` | Local KeyPulse-to-Obsidian bridge: `README.md`, `备忘-vault-layout.md`, `templates/` (daily-note, event-card, topic-card templates) |")
open(f"{OUT}/structure-tree.md", "w", encoding="utf-8").write("\n".join(L) + "\n")

# ---------------- dependency-map.md ----------------
edges = A["edges"]; top = A["top_edges"]
def pretty(u): return u.replace("root:", "") + (".py" if u.startswith("root:") else "/")
units = sorted({k.split(" -> ")[0] for k in edges} | {k.split(" -> ")[1] for k in edges})
out_c = collections.Counter(); in_c = collections.Counter(); out_n = collections.Counter(); in_n = collections.Counter()
for k, c in edges.items():
    a, b = k.split(" -> "); out_c[a] += c; in_c[b] += c; out_n[a] += 1; in_n[b] += 1
D = []
D.append("# KeyPulse dependency map\n")
D.append("Repo: `/Users/Harland/Go/keypulse` (read-only). Analysis script: `scratchpad/analyze.py` (python3 -I, `ast`, static). Raw output: `scratchpad/analysis.json`.\n")
D.append("## Method\n")
D.append("- Scope: the 205 `.py` files under `keypulse/`. `tests/`, `scripts/` and root scripts are not in the graph.")
D.append("- Unit = subpackage directory (`capture`, `pipeline`, ...). Root modules are `root:<name>` (`root:cli` = `keypulse/cli.py`). `root:__init__` = `keypulse/__init__.py`.")
D.append("- Each `import`/`from ... import` statement counted once per (source unit, target unit) pair. Relative imports resolved. Self-edges dropped.")
D.append("- `module-level` = the import is a top-level statement; the rest are lazy (inside functions/methods). Lazy imports are included in the main count.")
D.append("- Static only: `importlib`/string-based imports are invisible. Third-party = top-level module not in `sys.stdlib_module_names` and not `keypulse`.\n")

D.append("## 1. Internal adjacency (source -> target: total imports / module-level)\n")
D.append("| Source | Target | Total | Module-level |")
D.append("|---|---|---:|---:|")
for k in sorted(edges, key=lambda k: (-edges[k], k)):
    a, b = k.split(" -> ")
    D.append(f"| `{pretty(a)}` | `{pretty(b)}` | {edges[k]} | {top.get(k, 0)} |")
D.append(f"\nTotal internal import statements: {sum(edges.values())} ({sum(top.values())} module-level).\n")

D.append("## 2. Fan-in / fan-out per unit\n")
D.append("| Unit | Imports out (stmts) | Distinct targets | Imports in (stmts) | Distinct sources |")
D.append("|---|---:|---:|---:|---:|")
for u in sorted(units, key=lambda u: -(out_c[u] + in_c[u])):
    D.append(f"| `{pretty(u)}` | {out_c[u]} | {out_n[u]} | {in_c[u]} | {in_n[u]} |")
D.append("")

D.append("## 3. Cycles\n")
D.append("Strongly connected components (Tarjan) on the unit graph.\n")
D.append("- All imports (incl. lazy): " + "; ".join("{" + ", ".join(pretty(x) for x in c) + "}" for c in A["cycles_all"]))
D.append("- Module-level imports only: " + "; ".join("{" + ", ".join(pretty(x) for x in c) + "}" for c in A["cycles_top"]))
D.append("- File-level: no file-level cycle found among resolved files.")
D.append("- Concrete 2-cycles: `config <-> utils` = `keypulse/utils/dates.py:30` (lazy `from keypulse.config import Config`) and `keypulse/config.py:296` (lazy `from keypulse.utils.env import load_dotenv_quiet`). `capabilities <-> capture` = `capture/watchers/browser_url.py:7` and `capabilities/builtin/browser_automation.py:169` (lazy).")
D.append("- Large SCC paths (checked against the edge table): `pipeline <-> obsidian` (pipeline->obsidian 4, obsidian->pipeline 3); `quality <-> obsidian` (2 each way); `pipeline -> hud -> sources -> quality -> pipeline`; `hud -> health -> root:app -> pipeline -> hud`; `root:app -> services -> obsidian`, which closes the loop through `pipeline`.\n")

D.append("## 4. Third-party imports per unit\n")
D.append("Import statements by top-level module (from `ast`, includes lazy imports).\n")
D.append("| Unit | Third-party modules (count) |")
D.append("|---|---|")
for u in sorted(A["third"], key=lambda u: pretty(u)):
    D.append(f"| `{pretty(u)}` | " + ", ".join(f"{m} ({c})" for m, c in sorted(A["third"][u].items(), key=lambda x: -x[1])) + " |")
D.append("\nNo third-party imports (stdlib + keypulse only): `store`, `utils`, `privacy`, `search`, `services`, `obsidian`, `quality`, `health`, `prompts`, `integrations`, `observability`, `root:app`, `root:i18n`.\n")

D.append("## 5. pyobjc / Cocoa-binding files\n")
pf = A["pyobjc_files"]
files = sorted({f for f, _, _ in pf})
D.append(f"**{len(files)} files under `keypulse/`** import a pyobjc-family module (AppKit, ApplicationServices, Quartz, objc, Foundation, Vision, WebKit, CoreFoundation), with 1 more in `tests/test_browser_discovery.py`. This is **17, not the ~25 quoted**. Not reconciled with the earlier figure.\n")
D.append("| File | Line | Module |")
D.append("|---|---:|---|")
for f, ln, m in sorted(pf):
    D.append(f"| `{f}` | {ln} | {m} |")
D.append("")

D.append("## 6. Top coupling blockers (file:line verified)\n")
D.append("1. **pipeline/__init__.py:1-43 eager imports.** Lines 1-15 import aggregate, contracts, feedback, model, record, themes, surface, policy at package import time, so importing any `keypulse.pipeline.*` submodule loads the whole pipeline. Consequence: `pipeline` is effectively unmovable without a lazy `__init__`.")
D.append("2. **sources -> quality; quality -> obsidian.** `keypulse/sources/sink.py:11` imports `keypulse.quality.entity_extractor`. `keypulse/quality/strategies/cluster/s003_reject_url_like.py:1` and `s004_reject_file_path.py:1` import `keypulse.obsidian.layout.slugify`. The quality framework pulls the Obsidian layout module in.")
D.append("3. **capture <-> capabilities.** `keypulse/capture/watchers/browser_url.py:7` imports `keypulse.capabilities.builtin.browser_automation` at module top. The back-edge is `capabilities/builtin/browser_automation.py:169` (lazy import of `capture.watchers.browser_discovery`, which pulls AppKit). `keypulse/capabilities/builtin/__init__.py:20-32` imports the builtin capabilities (line 33 commented out), and `:68` `register_builtin_capabilities()` registers them all, so importing any builtin loads the whole set.")
D.append("4. **app.py and cli.py hard-wired to pipeline/obsidian.** `app.py:20` imports `pipeline.daily_orchestrator.run_daily_after_obsidian_sync`; `app.py:156` imports `pipeline.load_model_gateway`; `app.py:276` and `app.py:305` import `pipeline.triggers`. `cli.py:52-85` imports triggers (52), services (53), search (54), capture (55), utils (56), hud (57), pipeline package (58-64), model (66), i18n (67), model_keychain (68), daily_orchestrator (76), daily_summary (77), onboarding (78), weekly_orchestrator (85).")
D.append("5. **DB schema ownership split.** `keypulse/store/migrations.py:4` `SCHEMA_VERSION = 19`. Legacy tables still created there: `profile_identity` (:137), `profile_entities` (:140), `profile_projects` (:143), `hourly_summaries` (:149), `daily_skeletons` (:159). Tables created outside the store: `keypulse/pipeline/triggers.py:49` `llm_trigger_log` (plus index at :60) and `keypulse/pipeline/run_record.py:236` `run_records` (plus index at :251).")
D.append("6. **hud <-> pipeline and health -> pipeline.** `keypulse/health/self_heal.py:17` imports `pipeline.daily_orchestrator.run_daily`. `hud -> pipeline` (`hud/summary.py:19` and `:305`, `pipeline.surface`) and `pipeline -> hud` (`pipeline/weekly_orchestrator.py:20`, `hud.state`) form a 2-cycle at unit level.\n")

D.append("## 7. Classification (copied from earlier scan) with checked evidence\n")
D.append("Classification copied as given. Evidence column = facts checked in this pass, from the AST data and file reads.\n")
def ev(u):
    o = ", ".join(f"{pretty(k.split(' -> ')[1])}({v})" for k, v in sorted(edges.items()) if k.split(' -> ')[0] == u)
    i = ", ".join(f"{pretty(k.split(' -> ')[0])}({v})" for k, v in sorted(edges.items()) if k.split(' -> ')[1] == u)
    return o or "none", i or "none"
rows = [
 ("EXTRACT-AS-IS", "search/", "search", "search", "Outgoing: store (2). Incoming: root:cli (2). Clean dependency on store only."),
 ("EXTRACT-AS-IS", "privacy/", "privacy", "privacy", "Leaf: no internal imports."),
 ("EXTRACT-AS-IS", "permissions.py", "root:permissions", "root:permissions", "Outgoing: utils (1). Incoming: root:app, root:cli. Imports pyobjc (ApplicationServices, Quartz, CoreFoundation, objc)."),
 ("EXTRACT-WITH-REFACTOR", "store/", "store", "store", "Leaf for internal imports. Holds legacy profile_*/hourly_summaries/daily_skeletons (migrations.py:137-159), SCHEMA_VERSION 19 (migrations.py:4). llm_trigger_log and run_records are created outside store (see blocker 5)."),
 ("EXTRACT-WITH-REFACTOR", "capture/", "capture", "capture", "Imports capabilities (1), privacy, utils, store, root:config. Imports pyobjc in 9 files (AppKit, Quartz, ApplicationServices, objc)."),
 ("EXTRACT-WITH-REFACTOR", "sources/", "sources", "sources", "sink.py:11 imports keypulse.quality.entity_extractor (verified)."),
 ("EXTRACT-WITH-REFACTOR", "config.py + utils/", "root:config", "utils", "Cycle: utils/dates.py:30 <-> config.py:296 (lazy)."),
 ("EXTRACT-WITH-REFACTOR", "services/", "services", "services", "services -> obsidian (1), store (5), utils (2). Obsidian coupling is the item to check; export.py itself was not read in this pass."),
 ("UNCLEAR", "quality/", "quality", "quality", "Imports obsidian (2), pipeline (1), sources (1). Blocker 2 (s003/s004 -> obsidian.layout) verified."),
 ("UNCLEAR", "health/", "health", "health", "self_heal.py:17 imports pipeline.daily_orchestrator (verified). Imports capabilities (5), store (6), utils (7)."),
 ("UNCLEAR", "pipeline/model.py", None, None, "Not split out in this scan (package-level only). Model gateway is imported by app.py:156 and pipeline/__init__.py:11."),
 ("UNCLEAR", "model_keychain.py", None, None, "Not a root file; lives in pipeline/. Imported by cli.py:68. Not analysed further."),
 ("UNCLEAR", "triggers.py", None, None, "Lives at pipeline/triggers.py (not root). Owns llm_trigger_log (triggers.py:49). Imported by app.py:276,305 and cli.py:52."),
 ("UNCLEAR", "capabilities/builtin/llm_backend.py", "capabilities", "capabilities", "Not analysed in this pass beyond the package-level edges (capabilities -> root:config (2), store (7))."),
 ("STAY-IN-LEGACY", "pipeline/ (rest)", "pipeline", "pipeline", "pipeline imports 10 packages; biggest fan-out: utils (16), prompts (10), root:config (9). Imported by root:cli (12), root:app (4), health, hud, obsidian, quality."),
 ("STAY-IN-LEGACY", "obsidian/", "obsidian", "obsidian", "Imports pipeline (3), quality (2), store, utils, root:i18n."),
 ("STAY-IN-LEGACY", "hud/", "hud", "hud", "Pyobjc: AppKit, Foundation, WebKit, objc (hud/app.py, hud/approval_window.py). Imports capabilities, capture, health, pipeline, sources, store."),
 ("STAY-IN-LEGACY", "cli.py", "root:cli", "root:cli", "Wires nearly everything (see blocker 4). Imports pyobjc in cli.py (AppKit, Quartz, ApplicationServices, Vision)."),
 ("STAY-IN-LEGACY", "app.py", "root:app", "root:app", "Hard-wired to pipeline triggers and orchestrators (blocker 4)."),
 ("STAY-IN-LEGACY", "prompts/", "prompts", "prompts", "Leaf for internal imports; consumed by pipeline (10)."),
 ("STAY-IN-LEGACY", "i18n.py", "root:i18n", "root:i18n", "Used by root:cli, obsidian, pipeline."),
 ("STAY-IN-LEGACY", "integrations/", "integrations", "integrations", "Outgoing: root:config (1). Consumed by pipeline (2), root:app (1), root:cli (1)."),
 ("STAY-IN-LEGACY", "observability/", "observability", "observability", "Leaf for internal imports; consumed by health, pipeline."),
]
D.append("| Class | Item | Outgoing internal (stmts) | Incoming internal (stmts) | Evidence / note |")
D.append("|---|---|---|---|---|")
for cls, item, ukey, ukey2, note in rows:
    o, i = ev(ukey) if ukey else ('n/a (package-level)', 'n/a')
    D.append(f"| {cls} | {item} | {o} | {i} | {note} |")
D.append("")
D.append("Note: `pipeline/model.py`, `model_keychain.py` and `triggers.py` are all under `keypulse/pipeline/` (the earlier list gave no prefix). Their rows show package-level edges only.\n")

D.append("## 8. Direct connections to keypulse.db\n")
D.append("Default path: `keypulse/config.py:11` `db_path = \"~/.keypulse/keypulse.db\"`; resolved by `keypulse/utils/paths.py:11` (`get_data_dir() / \"keypulse.db\"`). Canonical connection factory: `keypulse/store/db.py:26`. Entries marked `db_path` use a `db_path` variable; its origin was not traced per site.\n")
D.append("| File:line | Context |")
D.append("|---|---|")
for loc, ctx in [
 ("keypulse/store/db.py:26", "canonical `_get_conn()` (store factory)"),
 ("keypulse/app.py:215", "sqlite3.connect(db_path)"),
 ("keypulse/pipeline/signals.py:116", "db_path parameter"),
 ("keypulse/pipeline/run_record.py:232", "self.db_path (run_records)"),
 ("keypulse/pipeline/triggers.py:82,114,141,168,197", "db_path (llm_trigger_log), 5 call sites"),
 ("keypulse/pipeline/daily_orchestrator.py:542", "db_path parameter"),
 ("keypulse/pipeline/evidence.py:58,176,194", "db_path parameter, 3 call sites"),
 ("keypulse/capabilities/builtin/obsidian_sync_freshness.py:41", "db_path parameter (not traced)"),
 ("keypulse/obsidian/exporter.py:178", "db_path; exporter.py:120 returns `_keypulse_home() / \"keypulse.db\"`"),
]:
    D.append(f"| `{loc}` | {ctx} |")
D.append("\nNot keypulse.db (external sources, mostly read-only `mode=ro` URIs): `sources/plugins/chrome_history.py:77`, `safari_history.py:64`, `approved_sqlite.py:145,237,500`, `knowledgec.py:115`; `sources/discoverers/sqlite.py:158,181,206` connects to discovered sqlite files with `uri=False` (not read-only).\n")

D.append("## 9. Not verified / caveats\n")
D.append("- Pyobjc count: 17 under `keypulse/` (AST), not ~25.")
D.append("- Classification items for `services/export.py`, `capabilities/builtin/llm_backend.py`, `pipeline/model.py`, `model_keychain.py` are classification-only; file bodies were not read in this pass.")
D.append("- Per-site origin of `db_path` (section 8) not traced except the ones cited.")
D.append("- Static analysis misses dynamic imports and any `importlib` loading; `capabilities/builtin/__init__.py` registration is by explicit import, so it is covered.")
open(f"{OUT}/dependency-map.md", "w", encoding="utf-8").write("\n".join(D) + "\n")
print("ok")
