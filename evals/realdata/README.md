# Real-data control eval (KeyPulse arm vs bash arm)

Re-runnable version of the M1 acceptance eval (decision D29). Same host (Claude Code headless), same
questions, one attempt each:

- **kp arm**: only `mcp__keypulse__*` tools, against a *copy* of your real DB, through a per-question MCP
  config and a fake `HOME` whose `~/.keypulse/config.toml` points at the copy.
- **bash arm**: Bash/Read/Grep/Glob only, wrapped in `sandbox-exec` with a generated deny-read profile
  (KeyPulse dir, the eval data dir, and any `--deny-path` / `--deny-regex`).

Neither arm persists a session (`--no-session-persistence`), so one question cannot leak into the next.

## Data never goes in this repo

This directory holds **code only**. Questions, gold answers, DB copies, tool-call logs and reports all live
on your machine, by default under `~/.keypulse/evals/`:

```
~/.keypulse/evals/
  questions.jsonl          # you write this
  runs/<UTC timestamp>/    # keypulse.db (copy), home/, results-*.jsonl, judged-*.jsonl,
                           # replay.json, bash_arm.sb, report.md
```

`--data-dir` overrides the location, but the tool refuses any path inside a git work tree. Never commit
questions, answers, results or reports (only aggregate numbers may be quoted, see `no-real-data-in-repo`).
Tests use synthetic DBs and a fake `claude`; never point them at real data.

## Question file (`questions.jsonl`, one JSON object per line)

| field | required | meaning |
|---|---|---|
| `id` | yes | unique id |
| `group` | yes | free label for slicing (e.g. `A`, `D`) |
| `grant` | yes | list of buckets the KeyPulse arm may see, e.g. `["Work"]` |
| `query` | yes | the question given to the agent |
| `gold_answer` | yes | the reference answer (given to the judge only) |
| `expect` | no | `answer` (default), `not_found`, or `must_not_see` |
| `gold_event_ids` | no | event ids (`123` or `evt_123`); with `must_not_see`, tool output must contain none |
| `gold_match` | no | text hint(s) (string or list); used for the contamination scan and `must_not_see` |
| `human_verdicts` | no | e.g. `{"kp": "correct", "bash": "wrong"}` pre-labels, to measure judge agreement |

`must_not_see` needs `gold_event_ids` or `gold_match`, runs on the kp arm only, and is graded mechanically
from the replay (no LLM).

## Run

Requires `claude` (Claude Code CLI) and `keypulse` on PATH; the bash arm needs macOS `sandbox-exec`.

```bash
# 1. Prepare: consistent DB copy (sqlite3 backup API), remove transcripts written after you authored
#    the questions, then a contamination scan that prints counts only.
python -m evals.realdata prepare \
    --exclude-session <build-session-id> --authored-after 2026-10-01T00:00:00   # --db defaults to the configured DB

# 2. Everything else in one command (kp arm, bash arm, replay, judge, report):
python -m evals.realdata all --deny-regex <build-session-id>
#    ...or step by step:
python -m evals.realdata run --arm kp
python -m evals.realdata run --arm bash --deny-regex <build-session-id>
python -m evals.realdata replay
python -m evals.realdata judge --judge-model sonnet
python -m evals.realdata report            # writes <run dir>/report.md
```

Common flags: `--data-dir`, `--questions`, `--run-dir` (default: latest run), `--only ID...`.
`run` is resumable: question ids already in `results-<arm>.jsonl` are skipped. `judge` resumes the same way.

- `--exclude-session` / `--authored-after` delete `claude_code` and `codex_cli` rows only. A session id matches
  `session_id`, `external_id`, `metadata_json` or an evidence locator. `--authored-after` compares against
  `ts_start` as a string, so use the stored ISO format.
- Contamination: any non-zero count means gold strings still sit in transcript rows the kp arm can search.
  Fix the exclusion before trusting the result.
- Pass the same session ids to the bash arm via `--deny-regex`; the profile is written to `<run dir>/bash_arm.sb`.
  It is a sandbox-exec regex, so escape regex metacharacters yourself.

## What the pieces do

- **judge**: calls `claude -p` with a fixed prompt, no tools, no session persistence (model via `--judge-model`).
  It must return strict JSON `{"verdict": "correct|partial|wrong", "reason": "..."}`. Malformed output becomes an
  `error` verdict, never `correct`. For `not_found`, correct means the answer says not found and presents nothing
  as the answer. A run with no answer is also an `error`.
- **replay**: re-runs recorded KeyPulse tool calls directly against the DB copy, recording latency (p50/p95),
  scanning output with KeyPulse's sensitive-content detectors plus an ID-number regex, and checking every
  returned `bucket` against the question's grant.
- **report**: per-arm accuracy (correct / graded; errors count against), partial/wrong/error counts, exclusive
  correct both ways, median/max seconds, median tool calls, cost, tool latency, leak hits, bucket violations and
  judge-vs-human disagreements. It contains ids and numbers only, no question or answer text. Its output stays in the run dir.

Judge agreement with human pre-labels needs `human_verdicts` on the questions; spot-check judge reasons in
`judged-*.jsonl` before relying on the numbers.

Tokens are created in-process against the DB copy, written only to a short-lived 0600 MCP config in a temp dir
that is deleted after each question, and are never printed or stored in results.
