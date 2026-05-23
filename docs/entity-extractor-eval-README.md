# Entity Extractor Eval Harness

- Dry run（不走 DB/LLM）：`python -m scripts.eval_entity_extractor --dry-run --json-out /tmp/entity-extractor-eval-dryrun.json --md-out /tmp/entity-extractor-eval-dryrun.md`
- 真 7 天评测（固定样本日，输出到 docs）：`python -m scripts.eval_entity_extractor`
- 可自定义日期：`python -m scripts.eval_entity_extractor --dates 2026-05-19 2026-05-07 2026-05-13 2026-05-21 2026-05-06 2026-05-11 2026-05-20`
- 当前 Codex sandbox 禁外网，真 LLM 跑数请在 host 环境执行。
