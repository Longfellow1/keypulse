<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **keypulse** (10292 symbols, 17189 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/keypulse/context` | Codebase overview, check index freshness |
| `gitnexus://repo/keypulse/clusters` | All functional areas |
| `gitnexus://repo/keypulse/processes` | All execution flows |
| `gitnexus://repo/keypulse/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## 输出质量标准 — Gold Set

任何用户可见的 LLM 产出（daily / weekly / skill propose / 其他直接给用户看的总结）统一走 Gold Set 流程，不允许"一版定稿"或"改完 prompt 没对照就上线"。

### 流程

1. **起 2-3 个候选版本**：Opus 4.7 用不同 prompt / 视角 / 详略各起一版
2. **人工对照微调**：挑出最佳版，标记 anti-pattern（套话、流水话、堆专有词等）
3. **固化为 gold set baseline**：作为该任务的退化检查锚点
4. **后续 prompt 改动跟 baseline 比**：通过 daily_validator / weekly_validator 之类的工具确认不退化才能合并

### 禁止

- 一版直接定稿
- 没经过人工对照就上线 prompt
- 改 prompt 不跑 validator 对照 baseline

### 现有 gold set

- `daily` — `docs/golden-daily/2026-05-06.md`（黄金 baseline，validator score ≥ 97 不退化）+ `2026-05-09.md`
- `weekly` — `docs/golden-weekly/2026-W19-exec.md` + `2026-W19-plain.md`（M5 内容修后会重起 baseline）
- `skill propose` — 待 v0 hello world 跑通后建立