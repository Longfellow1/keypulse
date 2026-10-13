"""CLI: python -m evals.realdata {prepare,validate,run,replay,judge,report,all} ...  (see README.md)."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from evals.realdata import judge as judge_mod
from evals.realdata import replay as replay_mod
from evals.realdata import report as report_mod
from evals.realdata import runner
from evals.realdata import validate as validate_mod
from evals.realdata.paths import latest_run_dir, new_run_dir, resolve_data_dir
from evals.realdata.prepare import backup_copy, contamination_counts, strip_transcripts
from evals.realdata.questions import QuestionError, load_questions
from evals.realdata.sandbox import build_profile


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data-dir", help="eval data dir (default ~/.keypulse/evals); never inside the repo")
    p.add_argument("--questions", help="questions JSONL (default <data-dir>/questions.jsonl)")
    p.add_argument("--run-dir", help="run dir (default: latest under <data-dir>/runs)")
    p.add_argument("--only", nargs="*", default=[], help="restrict to these question ids")


def _codex_opt(p: argparse.ArgumentParser) -> None:
    p.add_argument("--codex-bin", default=runner.CODEX_BIN, help="Codex CLI binary (used when a host is codex)")


def _judge_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--judge-host", choices=runner.HOSTS, default="claude")
    p.add_argument("--judge-model", help=f"default: {runner.DEFAULT_MODEL['claude']} (claude) or "
                                         f"{runner.DEFAULT_MODEL['codex']} (codex)")


def _run_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", choices=runner.HOSTS, default="claude",
                   help="CLI that runs the KeyPulse arm (codex: kp arm only)")
    p.add_argument("--model", help=f"default: {runner.DEFAULT_MODEL['claude']} (claude) or "
                                   f"{runner.DEFAULT_MODEL['codex']} (codex)")
    p.add_argument("--max-turns", type=int, default=25)
    p.add_argument("--timeout", type=int, default=900, help="seconds per question")
    p.add_argument("--keypulse-bin", default="keypulse")
    p.add_argument("--jobs", type=int, default=4,
                   help="questions run at once (each is its own isolated process; 1 = one by one)")
    p.add_argument("--claude-bin", default="claude")
    _codex_opt(p)
    p.add_argument("--sandbox-exec", default="sandbox-exec")
    p.add_argument("--deny-path", action="append", default=[], help="extra path the bash arm may not read (repeatable)")
    p.add_argument("--deny-regex", action="append", default=[],
                   help="SBPL regex of paths the bash arm may not read, e.g. a session id (repeatable)")


def _resolve_bin(name: str, what: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"{what} not found or not executable: {name}")
    return path


def _setup(args):
    data_dir = resolve_data_dir(args.data_dir)
    qpath = Path(args.questions).expanduser() if args.questions else data_dir / "questions.jsonl"
    try:
        qs = load_questions(qpath)
    except (QuestionError, OSError) as exc:
        raise SystemExit(f"cannot load questions: {exc}") from None
    if getattr(args, "only", None):
        qs = [q for q in qs if q.id in set(args.only)]
    return data_dir, qs


def _run_dir(args, data_dir: Path) -> Path:
    return Path(args.run_dir).expanduser() if args.run_dir else latest_run_dir(data_dir)


def cmd_prepare(args) -> int:
    data_dir, qs = _setup(args)
    db = Path(args.db).expanduser() if args.db else _configured_db()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")
    run_dir = new_run_dir(data_dir)
    copy = run_dir / "keypulse.db"
    backup_copy(db, copy)
    deleted = strip_transcripts(copy, args.exclude_session, args.authored_after)
    counts = contamination_counts(copy, qs)
    (run_dir / "prepare.json").write_text(json.dumps(
        {"deleted_rows": deleted, "contamination_counts": counts}, indent=2), encoding="utf-8")
    print(f"run dir: {run_dir}")
    print(f"deleted transcript rows: excluded sessions={deleted['sessions']}, after cutoff={deleted['after']}")
    dirty = {k: v for k, v in counts.items() if v}
    print(f"contamination check: {len(dirty)} of {len(counts)} questions still have gold strings in transcript rows")
    for qid, n in sorted(dirty.items()):
        print(f"  {qid}: {n} row(s)")
    gold = validate_mod.validate_gold(qs, copy)
    for line in validate_mod.format_lines(gold, problems_only=True):  # ids and reason codes only, no row text
        print(line)
    return 0


def cmd_validate(args) -> int:
    data_dir, qs = _setup(args)
    db = _run_dir(args, data_dir) / "keypulse.db"
    if not db.exists():
        raise SystemExit(f"run DB not found: {db} (run `prepare` first, or pass --run-dir)")
    results = validate_mod.validate_gold(qs, db)
    for line in validate_mod.format_lines(results):
        print(line)
    return 1 if validate_mod.has_failures(results) else 0


def _configured_db() -> Path:
    from keypulse.config import Config

    return Config.load().db_path_expanded


def _bash_profile(args, data_dir: Path, run_dir: Path) -> Path:
    deny = [Path("~/.keypulse").expanduser(), data_dir, *[Path(p) for p in args.deny_path]]
    prof = run_dir / "bash_arm.sb"
    prof.write_text(build_profile(deny, args.deny_regex), encoding="utf-8")
    return prof


def cmd_run(args, arm: str | None = None) -> int:
    arm = arm or args.arm
    try:
        runner.check_host(arm, args.host)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    data_dir, qs = _setup(args)
    run_dir = _run_dir(args, data_dir)
    prof = _bash_profile(args, data_dir, run_dir) if arm == "bash" else None
    keypulse_bin, codex = args.keypulse_bin, args.codex_bin
    if args.host == "codex":  # the wrapper script runs with a minimal env, so use absolute paths
        keypulse_bin = _resolve_bin(args.keypulse_bin, "keypulse")
        codex = _resolve_bin(args.codex_bin, "codex binary")
    runner.run_arm(arm, qs, run_dir, keypulse_bin=keypulse_bin, model=args.model, max_turns=args.max_turns,
                   timeout=args.timeout, sandbox_profile=prof, sandbox_exec=args.sandbox_exec, claude=args.claude_bin,
                   host=args.host, codex=codex, jobs=args.jobs)
    return 0


def cmd_replay(args) -> int:
    data_dir, qs = _setup(args)
    out = replay_mod.replay_kp({q.id: q for q in qs}, _run_dir(args, data_dir))
    print(f"replayed {out['calls']} calls; leaks={len(out['leaks'])} bucket_violations={len(out['bucket_violations'])}")
    return 0


def cmd_judge(args) -> int:
    data_dir, qs = _setup(args)
    run_dir = _run_dir(args, data_dir)
    codex = _resolve_bin(args.codex_bin, "codex binary") if args.judge_host == "codex" else args.codex_bin
    for arm in ("kp", "bash"):
        if (run_dir / f"results-{arm}.jsonl").exists():
            judge_mod.judge_arm(arm, {q.id: q for q in qs}, run_dir, model=args.judge_model, claude=args.claude_bin,
                                host=args.judge_host, codex=codex, jobs=args.jobs)
    return 0


def cmd_report(args) -> int:
    data_dir, qs = _setup(args)
    out = report_mod.build_report({q.id: q for q in qs}, _run_dir(args, data_dir))
    print(f"report: {out}")
    return 0


def cmd_all(args) -> int:
    if args.host == "codex":  # the bash arm is Claude-only, so one `all` cannot span both hosts
        raise SystemExit("all runs the bash arm too, and that arm stays on Claude. With codex, run "
                         "`run --arm kp --host codex`, then `run --arm bash`, then `replay`, `judge`, `report`.")
    if cmd_validate(args) != 0 and not args.allow_invalid_gold:
        print("stopped: gold validation failed (see FAIL lines). Fix the questions or the run, "
              "or pass --allow-invalid-gold to run anyway.", file=sys.stderr)
        return 1
    for arm in ("kp", "bash"):
        cmd_run(args, arm)
    cmd_replay(args)
    cmd_judge(args)
    return cmd_report(args)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m evals.realdata", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="copy the DB, strip post-authoring transcripts, contamination check")
    _common(p)
    p.add_argument("--db", help="source DB (default: the configured KeyPulse DB)")
    p.add_argument("--exclude-session", action="append", default=[], help="session id whose transcript rows are removed")
    p.add_argument("--authored-after", help="ISO timestamp; claude_code/codex_cli rows at/after it are removed")
    p.set_defaults(fn=cmd_prepare)

    p = sub.add_parser("run", help="run one arm")
    _common(p)
    _run_opts(p)
    p.add_argument("--arm", choices=["kp", "bash"], required=True)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("replay", help="replay KeyPulse tool calls: latency, leaks, buckets, must_not_see")
    _common(p)
    p.set_defaults(fn=cmd_replay)

    p = sub.add_parser("judge", help="grade results with the judge model")
    _common(p)
    _judge_opts(p)
    p.add_argument("--claude-bin", default="claude")
    p.add_argument("--jobs", type=int, default=4, help="answers graded at once (1 = one by one)")
    _codex_opt(p)
    p.set_defaults(fn=cmd_judge)

    p = sub.add_parser("validate", help="check gold_event_ids exist in the run DB and gold_match occurs in them")
    _common(p)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("report", help="write report.md into the run dir")
    _common(p)
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("all", help="validate gold, run both arms, replay, judge, report")
    _common(p)
    _run_opts(p)
    _judge_opts(p)
    p.add_argument("--allow-invalid-gold", action="store_true", help="continue even if gold validation has FAILs")
    p.set_defaults(fn=cmd_all)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
