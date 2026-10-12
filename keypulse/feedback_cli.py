"""`keypulse feedback list|export`: review the verdicts AI clients filed through rate_result, and turn
them into local eval-question drafts (evals/realdata questions.jsonl format, M2-0)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import click

from keypulse.config import Config
from keypulse.store import feedback
from keypulse.store.db import init_db

GOLD_PLACEHOLDER = "TODO: write the reference answer (the eval loader rejects an empty gold_answer)"


def _inside_git_worktree(path: Path) -> bool:
    probe = path.parent if not path.is_dir() else path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        r = subprocess.run(["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def _buckets(row: dict) -> list[str]:
    try:
        value = json.loads(row["grant_buckets"])
    except (TypeError, ValueError):
        return []
    return [b for b in value if isinstance(b, str) and b] if isinstance(value, list) else []


def draft_question(row: dict) -> dict | None:
    """One feedback row -> a questions.jsonl object, or None when it cannot become a question
    (no query context, or no grant buckets)."""
    buckets, query = _buckets(row), row.get("query")
    if not buckets or not query:
        return None
    gold_events = [f"evt_{row['event_id']}"] if row.get("event_id") is not None else []
    q: dict = {
        "id": f"fb_{row['id']}",
        "group": "feedback",
        "grant": buckets,
        "query": query,
        "gold_answer": GOLD_PLACEHOLDER,
        "feedback_verdict": row["verdict"],
    }
    if row["verdict"] == "wrong" and gold_events:
        q["expect"] = "must_not_see"   # the client says this result should not have been returned
    if gold_events:
        q["gold_event_ids"] = gold_events
    if row.get("note"):
        q["draft_note"] = row["note"]
    return q


@click.group(name="feedback", help="Review result feedback from AI clients and export eval-question drafts.")
def feedback_group() -> None:
    pass


@feedback_group.command("list", help="Counts per verdict and recent feedback (redacted).")
@click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
def list_command(limit: int) -> None:
    init_db(Config.load().db_path_expanded)
    counts = feedback.feedback_counts()
    click.echo("  ".join(f"{v}={counts[v]}" for v in feedback.VERDICTS))
    for r in feedback.feedback_rows(limit):
        ref = f"evt_{r['event_id']}" if r["event_id"] is not None else "-"
        query = feedback.clean_text(r["query"], feedback.MAX_QUERY_CHARS) or "-"
        note = feedback.clean_text(r["note"], feedback.MAX_NOTE_CHARS) or "-"
        click.echo(f"{r['id']}\t{r['created_at']}\t{r['client_id']}\t{r['verdict']}\t{ref}\tquery={query}\tnote={note}")


@feedback_group.command("export", help="Write question drafts (questions.jsonl format) outside any git work tree.")
@click.option("--out", "out", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Output .jsonl path.")
@click.option("--force", is_flag=True, help="Overwrite an existing file.")
def export_command(out: Path, force: bool) -> None:
    target = out.expanduser().resolve()
    if _inside_git_worktree(target):
        raise click.ClickException("refusing to write inside a git work tree: feedback drafts are private data")
    if target.exists() and not force:
        raise click.ClickException("output file exists; pass --force to overwrite")
    init_db(Config.load().db_path_expanded)
    drafts, skipped = [], 0
    for row in reversed(feedback.feedback_rows()):
        q = draft_question(row)
        if q is None:
            skipped += 1
        else:
            drafts.append(q)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        for q in drafts:
            fh.write(json.dumps(q, ensure_ascii=False) + "\n")
    click.echo(f"Wrote {len(drafts)} draft question(s) to {target} ({skipped} skipped: no query context).")
    click.echo("Fill in gold_answer for each before running the eval; drafts are never committed.")
