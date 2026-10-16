# Penpal project structure tree

Source: `/Users/Harland/Go/penpal` (read-only). LOC = `wc -l`. Totals count `.py` only unless noted.
Excluded: .git, .venv, build, dist, __pycache__, .claude, .codex-home, .sandbox-home, node_modules, *.log, .penpal (runtime data), plus .DS_Store, penpal.egg-info, .gitnexus, .pytest_cache, .ruff_cache (tooling caches, not project source).

## 0. Top level

| Entry | Content |
|---|---|
| `penpal/` | Python package, 205 `.py` files, 40,879 LOC (plus 29 md, 28 json, 1 yaml non-py files) |
| `tests/` | 164 `.py` files, 26,025 LOC, flat, plus `fixtures/` and `golden/` |
| `scripts/` | 12 `.py`, 1 `.sh`, 2 launchd plists (one-off migrations, evals, preflight) |
| `docs/` | 40 top-level files + 56 files in 10 subdirs (design, eval, prompt-candidate and review notes) |
| `brain/` | Project brain: 7 root pages + `pages/` (11 decision pages) |
| `integrations/` | launchd plists (3) and the Obsidian vault bridge (README, 3 templates, layout note) |
| root files | `app_main.py`, `setup_app.py`, `pyproject.toml`, `Makefile`, `install.sh`, `config.toml`, `README*.md`, `CLAUDE.md`, `AGENTS.md`, `BRAIN.md`, `CHANGELOG.md`, `TESTING.md`, `SUMMARY.md`, several `备忘-*.md` design notes, `config.toml.bak`/`.bak2` |

## 1. penpal/ package (file-level tree)

Format: `name  LOC  one-line purpose`. Purpose = first non-empty line of the module docstring (`(no docstring)` if none). Non-py files show their first heading line (md) or type.

```
penpal/  [205 .py, 40,879 LOC]
  __init__.py                              1  (no docstring)
  app.py                                 442  (no docstring)
  cli.py                                4645  (no docstring)
  config.py                              315  (no docstring)
  i18n.py                                 43  (no docstring)
  permissions.py                         157  macOS permission prompts.
  capabilities/  [16 .py, 1,454 LOC]
    __init__.py                             13  (no docstring)
    base.py                                 51  (no docstring)
    registry.py                            141  (no docstring)
    store.py                                66  Persistence bridge between CapabilityRegistry and the SQLite state repo.
    builtin/  [12 .py, 1,183 LOC]
      __init__.py                             70  Built-in capability registry.
      _common.py                             110  (no docstring)
      _watcher_health.py                     150  (no docstring)
      accessibility_permission.py             72  (no docstring)
      appkit_runtime.py                       33  (no docstring)
      browser_automation.py                  222  (no docstring)
      bundle_integrity.py                    114  (no docstring)
      health_freshness.py                     46  (no docstring)
      llm_backend.py                         126  (no docstring)
      obsidian_sync_freshness.py             127  (no docstring)
      pause_state.py                          30  (no docstring)
      screen_recording_permission.py          83  (no docstring)
  capture/  [28 .py, 3,939 LOC]
    __init__.py                              0  (no docstring)
    active_app.py                           60  (no docstring)
    aggregator.py                          120  (no docstring)
    base.py                                262  (no docstring)
    camera_monitor.py                      125  (no docstring)
    fusion.py                               88  (no docstring)
    manager.py                             697  (no docstring)
    merge.py                                42  (no docstring)
    normalizer.py                          269  (no docstring)
    policy.py                              110  (no docstring)
    provider.py                             41  (no docstring)
    user_presence.py                        60  User presence detection via macOS IOKit IOHIDIdleTime.
    providers/  [2 .py, 149 LOC]
      __init__.py                              1  (no docstring)
      vision_native.py                       148  (no docstring)
    watchers/  [14 .py, 1,916 LOC]
      __init__.py                              0  (no docstring)
      ax_text.py                             192  (no docstring)
      browser.py                             196  (no docstring)
      browser_discovery.py                   213  (no docstring)
      browser_history.py                      46  (no docstring)
      browser_url.py                         208  (no docstring)
      clipboard.py                            80  (no docstring)
      idle.py                                 59  (no docstring)
      keyboard_chunk.py                      173  (no docstring)
      keyboard_native.py                     166  (no docstring)
      manual.py                               17  (no docstring)
      ocr.py                                 227  (no docstring)
      window.py                              288  (no docstring)
      window_capture.py                       51  (no docstring)
  data/  [1 .py, 1 LOC]
    __init__.py                              1  Static data assets for Penpal.
    holidays.yaml                           93  YAML data (holiday calendar)
  health/  [6 .py, 985 LOC]
    __init__.py                              3  (no docstring)
    alerts.py                               82  (no docstring)
    check.py                               390  (no docstring)
    product_delivery.py                    197  (no docstring)
    report.py                               26  (no docstring)
    self_heal.py                           287  (no docstring)
  hud/  [8 .py, 2,516 LOC]
    __init__.py                             31  (no docstring)
    app.py                                 542  (no docstring)
    approval_window.py                     388  (no docstring)
    health.py                               47  (no docstring)
    monitor_html.py                        687  (no docstring)
    state.py                               167  (no docstring)
    state_reset.py                          30  (no docstring)
    summary.py                             624  (no docstring)
  integrations/  [3 .py, 170 LOC]
    __init__.py                              9  (no docstring)
    sinks.py                               135  (no docstring)
    state.py                                26  (no docstring)
  observability/  [1 .py, 12 LOC]
    watcher_tiers.py                        12  (no docstring)
  obsidian/  [7 .py, 1,585 LOC]
    __init__.py                              1  Obsidian export helpers.
    exporter.py                           1076  (no docstring)
    layout.py                               74  (no docstring)
    model.py                                18  (no docstring)
    principle_exporter.py                  274  (no docstring)
    quality_gate.py                        124  Daily 报告质量护栏：写入前评分，劣化时拒写。
    weekday.py                              18  (no docstring)
  pipeline/  [52 .py, 16,943 LOC]
    __init__.py                             43  (no docstring)
    aggregate.py                           103  (no docstring)
    anchor_gateway.py                      161  (no docstring)
    artifact_writer.py                      62  (no docstring)
    cache.py                                24  (no docstring)
    clustering.py                          284  (no docstring)
    contracts.py                            39  (no docstring)
    daily_orchestrator.py                 1948  (no docstring)
    daily_strategy.py                      776  Daily generation strategies.
    daily_summary.py                      1253  (no docstring)
    daily_validator.py                     513  Daily 5 类断言 validator — 设计文档 docs/golden-daily/SCHEMA.md。
    degraded_content.py                    106  (no docstring)
    dimension_stats.py                     222  (no docstring)
    entity_coherence_judge.py              322  Entity Coherence Judge — LLM-based eval harness for daily entity pollution detection.
    entity_extractor.py                    135  (no docstring)
    entity_extractor_llm.py                331  L1 Entity Extractor — standalone LLM module for raw-event entity mapping.
    entity_merger_llm.py                   348  L2 Entity Merger — cross-day canonical entity merge via LLM.
    event_intake.py                        563  (no docstring)
    event_scoring.py                        83  (no docstring)
    evidence.py                            229  (no docstring)
    feedback.py                             96  (no docstring)
    fragments.py                           531  (no docstring)
    gates.py                               139  (no docstring)
    holiday_strategy.py                    245  (no docstring)
    holiday_templates.py                    15  (no docstring)
    llm_errors.py                           92  (no docstring)
    low_activity.py                         25  低活动周识别（兜底节假日检测）。
    mine.py                                 74  (no docstring)
    model.py                              1266  (no docstring)
    model_card.py                           60  Tier resolution for daily generation strategy.
    model_keychain.py                      130  (no docstring)
    normalize.py                           104  (no docstring)
    onboarding.py                          139  (no docstring)
    overview.py                             48  (no docstring)
    policy.py                               46  (no docstring)
    principle_distillation.py              136  (no docstring)
    quality_score.py                        79  (no docstring)
    record.py                               93  (no docstring)
    rollup_orchestrator.py                  15  Rollup orchestrator facade - 抽象周/月/季/年报告。
    run_record.py                          276  (no docstring)
    session_renderer.py                    127  (no docstring)
    session_splitter.py                     55  (no docstring)
    signals.py                             229  (no docstring)
    surface.py                             273  (no docstring)
    themes.py                               76  (no docstring)
    thing.py                                19  (no docstring)
    topic_status.py                        219  (no docstring)
    triggers.py                            213  LLM-trigger decision module: fail-closed gateway for kernel's LLM calls.
    value_density.py                       137  Shared value_density calculation module for daily orchestrator and narrative sync paths.
    weekly_orchestrator.py                3365  (no docstring)
    weekly_topic_anchor.py                 605  主线锚定层 — daily 与 weekly 之间的运行时状态。
    weekly_validator.py                    471  (no docstring)
  privacy/  [3 .py, 157 LOC]
    __init__.py                              0  (no docstring)
    desensitizer.py                        102  (no docstring)
    detectors.py                            55  (no docstring)
  prompts/  [2 .py, 164 LOC]
    __init__.py                             13  (no docstring)
    loader.py                              151  (no docstring)
    备忘-L0-anchor.v1.md                      53  md: ---
    备忘-L1-cluster-review.v1.md              33  md: ---
    备忘-L1-entity-extractor.v1.md           169  md: ---
    备忘-L2-entity-merger.v1.md               72  md: ---
    备忘-L2-narrative.v2.md                  110  md: ---
    备忘-L3-topic-naming.v1.md                27  md: ---
    备忘-L4-weekly-reconcile.v1.md            46  md: ---
    备忘-L5-weekly-main-narrative.v1.md       44  md: ---
    备忘-L5-weekly-main-narrative.v2.md       54  md: ---
    备忘-L6-explorer.v1.md                    33  md: ---
    备忘-L7-principle-distillation.v1.md      44  md: ---
    备忘-L7-principle-distillation.v2.md      46  md: ---
    备忘-L8-entity-coherence-judge.v1.md      96  md: ---
    备忘-L9-entity-extractor-judge.v1.md     106  md: ---
    备忘-daily-flagship.v1.md                118  md: ---
    备忘-daily-flagship.v2.md                202  md: ---
    备忘-daily-flagship.v3.md                208  md: ---
    备忘-daily-flagship.v4.md                262  md: ---
    备忘-event-title-humanize.v1.md           16  md: ---
    candidates/  [0 .py, 0 LOC]
      备忘-L5-weekly-v3-a.md                    52  md: ---
      备忘-L5-weekly-v3-b.md                    53  md: ---
      备忘-L5-weekly-v3-c.md                    51  md: ---
      备忘-daily-flagship-v4-a.md              227  md: ---
      备忘-daily-flagship-v4-b.md              225  md: ---
      备忘-daily-flagship-v4-b2.md             227  md: ---
      备忘-daily-flagship-v4-b3.md             244  md: ---
      备忘-daily-flagship-v4-b4.md             245  md: ---
      备忘-daily-flagship-v4-b5.md             245  md: ---
      备忘-daily-flagship-v4-c.md              251  md: ---
    schemas/  [0 .py, 0 LOC]
      L0_anchor_input.json                    85  JSON data
      L0_anchor_output.json                   40  JSON data
      L1_entity_input.json                    55  JSON data
      L1_entity_output.json                   76  JSON data
      L1_input.json                          149  JSON data
      L1_output.json                          84  JSON data
      L2_entity_merger_input.json            135  JSON data
      L2_entity_merger_output.json            54  JSON data
      L2_input.json                           63  JSON data
      L2_output.json                          11  JSON data
      L3_input.json                           48  JSON data
      L3_output.json                          29  JSON data
      L4_input.json                           50  JSON data
      L4_output.json                          14  JSON data
      L5_input.json                           58  JSON data
      L5_output.json                          17  JSON data
      L6_input.json                           16  JSON data
      L6_output.json                          10  JSON data
      L7_principle_distillation_input.json    40  JSON data
      L7_principle_distillation_output.json    44  JSON data
      L8_input.json                           32  JSON data
      L8_output.json                          62  JSON data
      L9_input.json                          145  JSON data
      L9_output.json                         144  JSON data
      daily_flagship_input.json              156  JSON data
      daily_flagship_output.json              11  JSON data
      event_title_humanize_input.json         24  JSON data
      event_title_humanize_output.json        12  JSON data
  quality/  [16 .py, 415 LOC]
    __init__.py                              5  (no docstring)
    base.py                                 22  (no docstring)
    entity_extractor.py                     29  (no docstring)
    registry.py                             41  (no docstring)
    runner.py                               70  (no docstring)
    strategies/  [11 .py, 248 LOC]
      __init__.py                              3  (no docstring)
      cluster/  [10 .py, 245 LOC]
        __init__.py                             35  (no docstring)
        s001_reject_empty.py                    14  (no docstring)
        s002_reject_numeric_or_separator.py     16  (no docstring)
        s003_reject_url_like.py                 17  (no docstring)
        s004_reject_file_path.py                21  (no docstring)
        s005_reject_secret_pattern.py           17  (no docstring)
        s006_reject_command_flag.py             14  (no docstring)
        s007_reject_terminal_resolution.py      16  (no docstring)
        s008_reject_insufficient_content.py     18  (no docstring)
        s009_reject_command_and_title_slug.py    77  S009 – Reject command invocations and app-title slugs surfaced as topics.
  search/  [4 .py, 169 LOC]
    __init__.py                             11  (no docstring)
    backends.py                             85  (no docstring)
    engine.py                               42  (no docstring)
    query_builder.py                        31  (no docstring)
  services/  [5 .py, 246 LOC]
    __init__.py                              0  (no docstring)
    export.py                              118  (no docstring)
    sessionizer.py                          18  (no docstring)
    stats.py                                68  (no docstring)
    timeline.py                             42  (no docstring)
  sources/  [34 .py, 5,602 LOC]
    __init__.py                             15  (no docstring)
    approval.py                            207  (no docstring)
    discover.py                            432  (no docstring)
    registry.py                            151  (no docstring)
    scheduler.py                           441  (no docstring)
    sink.py                                263  (no docstring)
    types.py                               326  (no docstring)
    wechat_probe.py                        128  (no docstring)
    cleaning/  [6 .py, 312 LOC]
      __init__.py                             14  (no docstring)
      config.py                              110  (no docstring)
      content_quality.py                      51  (no docstring)
      dedup.py                                76  (no docstring)
      file_whitelist.py                       36  (no docstring)
      path_filter.py                          25  (no docstring)
    discoverers/  [7 .py, 975 LOC]
      __init__.py                             52  (no docstring)
      json_files.py                          132  (no docstring)
      jsonl.py                               172  (no docstring)
      leveldb.py                              99  (no docstring)
      markdown_vault.py                      154  (no docstring)
      plist.py                                89  (no docstring)
      sqlite.py                              277  (no docstring)
    plugins/  [13 .py, 2,352 LOC]
      __init__.py                             30  (no docstring)
      approved_sqlite.py                     562  (no docstring)
      chrome_history.py                      119  (no docstring)
      claude_code.py                         225  (no docstring)
      codex_cli.py                            89  (no docstring)
      git_log.py                             220  (no docstring)
      knowledgec.py                          192  (no docstring)
      leveldb_reader.py                      231  (no docstring)
      markdown_vault.py                      196  (no docstring)
      safari_history.py                      106  (no docstring)
      spotlight.py                           258  (no docstring)
      wechat.py                               45  (no docstring)
      zsh_history.py                          79  (no docstring)
  store/  [5 .py, 572 LOC]
    __init__.py                              0  (no docstring)
    db.py                                   39  (no docstring)
    migrations.py                          213  (no docstring)
    models.py                               81  (no docstring)
    repository.py                          239  (no docstring)
  utils/  [8 .py, 346 LOC]
    __init__.py                              0  (no docstring)
    atomic_io.py                            41  Atomic file-write helper: write to a temp file then os.replace into place.
    dates.py                                88  (no docstring)
    env.py                                  48  (no docstring)
    lock.py                                 64  (no docstring)
    logging.py                              32  (no docstring)
    paths.py                                27  (no docstring)
    text_filters.py                         46  Text filtering utilities for IME composition and noise detection.
```

### 1a. Per-subpackage totals (.py only)

| Subpackage | .py files | LOC |
|---|---:|---:|
| `pipeline/` | 52 | 16,943 |
| `penpal/` root files | 6 | 5,603 |
| `sources/` | 34 | 5,602 |
| `capture/` | 28 | 3,939 |
| `hud/` | 8 | 2,516 |
| `obsidian/` | 7 | 1,585 |
| `capabilities/` | 16 | 1,454 |
| `health/` | 6 | 985 |
| `store/` | 5 | 572 |
| `quality/` | 16 | 415 |
| `utils/` | 8 | 346 |
| `services/` | 5 | 246 |
| `integrations/` | 3 | 170 |
| `search/` | 4 | 169 |
| `prompts/` | 2 | 164 |
| `privacy/` | 3 | 157 |
| `observability/` | 1 | 12 |
| `data/` | 1 | 1 |
| **total** | **205** | **40,879** |

## 2. tests/ (flat, 164 .py files)

Grouped by the penpal subpackage each test imports most often (`penpal.<unit>` import count per file; `root:<x>` = root module `penpal/<x>.py`). Counts include `conftest.py`.

| Tested subpackage | Test files | LOC |
|---|---:|---:|
| `pipeline` | 41 | 8,748 |
| `sources` | 30 | 3,728 |
| `capture` | 19 | 2,626 |
| `root:cli` | 14 | 1,916 |
| `store` | 13 | 1,669 |
| `(no penpal import)` | 8 | 748 |
| `capabilities` | 6 | 1,003 |
| `root:config` | 6 | 545 |
| `obsidian` | 5 | 1,120 |
| `quality` | 4 | 638 |
| `hud` | 4 | 747 |
| `utils` | 3 | 121 |
| `root:i18n` | 3 | 970 |
| `root:app` | 2 | 537 |
| `health` | 2 | 506 |
| `integrations` | 2 | 93 |
| `privacy` | 1 | 266 |
| `prompts` | 1 | 44 |
| **total** | **164** | **26,025** |

`fixtures/` (package, not a test module):

- `tests/fixtures/__init__.py` (0 LOC)
- `tests/fixtures/things_pipeline.py` (139 LOC)
- `tests/fixtures/principle_distillation/ux_mental_model_alignment_chunks.json` (9 LOC)

`golden/`:

- `tests/golden/README.md` (7 LOC)
- `tests/golden/cluster_topics.jsonl` (43 LOC)
- `tests/golden/speaker_filtering.jsonl` (8 LOC)

## 3. scripts/

| File | LOC | Purpose |
|---|---:|---|
| `benchmark_m0_entities.py` | 68 | Minimal benchmark for M0 entity extraction overhead (100 events). |
| `com.penpal.daily.plist` | 44 | launchd job wrapping a `zsh -lc` daily run |
| `com.penpal.weekly.plist` | 33 | launchd job wrapping a `zsh -lc` weekly run |
| `eval_daily_flagship_v4_candidates.py` | 231 | Evaluate daily_flagship v4 candidate prompts on a real day (2026-05-06). |
| `eval_entity_extractor.py` | 905 | Entity extractor LLM-as-judge multi-day eval harness. |
| `eval_l5_weekly_v3_candidates.py` | 380 | Evaluate L5 weekly v3 prompt candidates on a target ISO week. |
| `lint_topics.py` | 345 | Lint and optionally clean topic notes under <vault>/Topics. |
| `migrate_anchor_display.py` | 166 | 一次性清洗 ~/.penpal/anchors.json 里 display 字段的「完成XX」前缀。 |
| `migrate_to_penpal_dir.py` | 181 | Migrate vault Events/Topics to ~/.penpal/events|topics. |
| `migrate_v0_entities.py` | 207 | Backfill raw_events.metadata_json.entities for M0. |
| `migrate_weekly_anchor_to_anchors.py` | 56 | One-off migration: ~/.penpal/weekly-anchor.json -> ~/.penpal/anchors.json. |
| `preflight_app.py` | 92 | Preflight check for the built .app bundle via capability registry. |
| `scrub_history.py` | 379 | One-time backfill scrub for historical raw_events. |
| `skeleton_mvp.py` | 344 | 骨架 MVP（叙事骨架 v0） |
| `smoke_py2app.sh` | 40 | Smoke test: runs the built `/Applications/Penpal.app` binary (py2app bundle) |

## 4. docs/ (by subdir, recursive file counts)

| Subdir | Files | Purpose (from file names) |
|---|---:|---|
| `docs/analysis/` | 1 | Endgame / distillation strategy analysis |
| `docs/daily-prompt-candidates/` | 3 | Daily prompt candidates A/B/C write-up (earlier round) |
| `docs/daily-prompt-candidates-v4/` | 20 | Daily flagship v4 candidate runs (B2-B5 x runs) + meta.json |
| `docs/golden-daily/` | 4 | Daily Gold Set baselines (2026-05-06 v3/v4, 2026-05-09) and schema |
| `docs/golden-weekly/` | 8 | Weekly Gold Set (W19 exec/plain, W22 postfix) and golden spec |
| `docs/images/` | 1 | HUD v1 screenshot |
| `docs/pk-weekly-w19/` | 3 | W19 weekly outputs from three model backends (deepseek, doubao, ernie) |
| `docs/reviews/` | 5 | External review rounds 2026-04-25 and 2026-04-30 (+ judgment notes) |
| `docs/superpowers/` | 1 | Design spec(s) under superpowers/specs |
| `docs/weekly-prompt-candidates-v3/` | 10 | Weekly v3 candidates a/b/c, run1 exec/plain + meta.json |
| `docs/` (top level) | 40 | Design notes, PRD (`Penpal_V1_PRD.md`), HUD mockups, eval JSON/MD, 说明/设计/记录 notes |

## 5. brain/

| Path | Purpose |
|---|---|
| `brain/index.md` | Brain index (catalog of root pages and pages/) |
| `brain/architecture.md` | System architecture |
| `brain/background.md` | Project background |
| `brain/flow.md` | Key flows |
| `brain/mindmap.md` | Feature mindmap |
| `brain/roadmap.md` | Roadmap |
| `brain/stack.md` | Tech stack |
| `brain/pages/` (11) | Decision pages: anchors.json as SSOT, beat vs emit separation, capability fact vs probe, daily single render entry, DB-direct read over screen capture, Gold Set gated prompt changes, knowledge-graph layer (partial), py2app preflight smoke test, skill layer (planned), TCC refresh on install, user curates / LLM proposes |

## 6. integrations/

| Path | Purpose |
|---|---|
| `integrations/launchd/` (3 plists) | `com.penpal.healthcheck` (StartInterval, runs `Penpal healthcheck`); `com.penpal.obsidian-sync-hourly` (StartInterval, `penpal obsidian`); `com.penpal.obsidian-sync` (StartCalendarInterval, `penpal obsidian`) |
| `integrations/obsidian/` | Local Penpal-to-Obsidian bridge: `README.md`, `备忘-vault-layout.md`, `templates/` (daily-note, event-card, topic-card templates) |
