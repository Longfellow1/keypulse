from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, time, timezone
from pathlib import Path

import click

from keypulse.sources.approval import ApprovalRecord, ApprovalStore
from keypulse.sources.discoverers import CandidateSource, discover_all_candidates
from keypulse.sources.registry import discover_all, get_source, list_sources, read_all
from keypulse.sources.types import DataSourceInstance, SemanticEvent


@click.group(name="sources")
def sources_group() -> None:
    pass


@sources_group.command("list")
def list_command() -> None:
    for plugin in list_sources():
        desc = getattr(plugin, "description", "") or ""
        click.echo(f"{plugin.name}\t[{plugin.privacy_tier}/{plugin.liveness}]\t{desc}")


@sources_group.command("discover")
def discover_command() -> None:
    discovered = discover_all()
    plugins = list_sources()
    recognized = [(plugin, discovered.get(plugin.name, [])) for plugin in plugins if discovered.get(plugin.name, [])]
    missing = [plugin.name for plugin in plugins if not discovered.get(plugin.name, [])]
    excluded = {instance.locator for _, instances in recognized for instance in instances}
    candidates = _flatten_candidates(discover_all_candidates(exclude_paths=excluded))
    store = ApprovalStore()

    click.echo("✅ 已识别（精读 plugins）：")
    if not recognized:
        click.echo("  (无)")
    for plugin, instances in recognized:
        click.echo(f"  {plugin.name}: {_recognized_summary(plugin.name, instances)}")

    click.echo("")
    click.echo("🟡 候选金矿（通用扫描，需用户确认）：")
    _print_candidates(candidates, store=store)

    click.echo("")
    click.echo("❌ 未发现：")
    if not missing:
        click.echo("  (无)")
    for name in missing:
        click.echo(f"  {name}")


@sources_group.command("candidates")
def candidates_command() -> None:
    candidates = _load_candidates()
    _print_candidates(candidates, store=ApprovalStore())


@sources_group.command("approve")
@click.argument("candidate_id")
@click.option("--note", default="", help="Optional approval note")
def approve_command(candidate_id: str, note: str) -> None:
    store = ApprovalStore()
    candidates = _load_candidates()
    candidate = _find_candidate_by_id(candidate_id, candidates, store=store)
    if candidate is None:
        raise click.ClickException(f"candidate id not found: {candidate_id}")

    normalized_id = store.candidate_id(candidate)
    if store.status(normalized_id) == "rejected":
        click.echo(f"warning: candidate {normalized_id} was rejected, resetting to approved")

    record = store.approve(candidate, note=note)
    click.echo(
        f"[✅] id={record.candidate_id} [{candidate.discoverer}/{candidate.confidence}] "
        f"{_display_path(candidate.path)} ({candidate.app_hint or '?'})"
    )


@sources_group.command("reject")
@click.argument("candidate_id")
@click.option("--reason", default="user_choice", help="Reject reason")
def reject_command(candidate_id: str, reason: str) -> None:
    store = ApprovalStore()
    candidates = _load_candidates()
    candidate = _find_candidate_by_id(candidate_id, candidates, store=store)
    if candidate is None:
        raise click.ClickException(f"candidate id not found: {candidate_id}")

    normalized_id = store.candidate_id(candidate)
    if store.status(normalized_id) == "approved":
        click.echo(f"warning: candidate {normalized_id} was approved, resetting to rejected")

    record = store.reject(candidate, reason=reason)
    click.echo(
        f"[❌] id={record.candidate_id} [{candidate.discoverer}/{candidate.confidence}] "
        f"{_display_path(candidate.path)} ({candidate.app_hint or '?'}) reason={reason}"
    )


@sources_group.command("unset")
@click.argument("candidate_id")
def unset_command(candidate_id: str) -> None:
    store = ApprovalStore()
    normalized_id = candidate_id.strip().lower()
    store.unset(normalized_id)
    click.echo(f"[新] id={normalized_id} status cleared")


@sources_group.command("list-approved")
def list_approved_command() -> None:
    records = ApprovalStore().list_approved()
    _print_approval_records(records, status_symbol="✅", note_label="note")


@sources_group.command("list-rejected")
def list_rejected_command() -> None:
    records = ApprovalStore().list_rejected()
    _print_approval_records(records, status_symbol="❌", note_label="reason")


@sources_group.command("read")
@click.option("--source", "source_name", default=None, help="Only read from one plugin")
@click.option("--since", default=None, help="ISO date/datetime, default: today 00:00 local")
@click.option("--until", default=None, help="ISO date/datetime, default: now")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output JSON Lines")
def read_command(source_name: str | None, since: str | None, until: str | None, as_json: bool) -> None:
    if source_name and get_source(source_name) is None:
        raise click.ClickException(f"unknown source: {source_name}")

    since_dt = _parse_bound(since, is_since=True)
    until_dt = _parse_bound(until, is_since=False)
    if until_dt < since_dt:
        raise click.ClickException("until must be >= since")

    for event in read_all(since_dt, until_dt, source=source_name):
        if as_json:
            click.echo(json.dumps(_event_to_json_dict(event), ensure_ascii=False))
            continue
        click.echo(
            f"{event.time.isoformat()}\t{event.source}\t{event.actor}\t{event.intent}\t{event.artifact}"
        )


def _parse_bound(raw: str | None, *, is_since: bool) -> datetime:
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    now_local = datetime.now(local_tz)

    if raw is None:
        if is_since:
            return datetime.combine(now_local.date(), time.min, tzinfo=local_tz).astimezone(timezone.utc)
        return now_local.astimezone(timezone.utc)

    if len(raw) == 10:
        day = datetime.fromisoformat(raw)
        if is_since:
            local_value = datetime.combine(day.date(), time.min, tzinfo=local_tz)
        else:
            local_value = datetime.combine(day.date(), time.max, tzinfo=local_tz)
        return local_value.astimezone(timezone.utc)

    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(timezone.utc)


def _event_to_json_dict(event: SemanticEvent) -> dict[str, object]:
    data = asdict(event)
    data["time"] = event.time.isoformat()
    return data


def _recognized_summary(plugin_name: str, instances: list[DataSourceInstance]) -> str:
    if plugin_name == "git_log":
        return f"{len(instances)} 个仓库"
    if plugin_name == "claude_code":
        root = Path(instances[0].locator).parent if instances else Path.home() / ".claude" / "projects"
        return f"{_display_path(str(root))} ({len(instances)} 个项目)"
    if plugin_name == "markdown_vault" and instances:
        label = instances[0].label
        return f"{_display_path(instances[0].locator)} ({label})"
    if len(instances) == 1:
        return _display_path(instances[0].locator)
    return f"{_display_path(instances[0].locator)} ... ({len(instances)} 个实例)"


def _flatten_candidates(candidates: dict[str, list[CandidateSource]]) -> list[CandidateSource]:
    flat: list[CandidateSource] = []
    for values in candidates.values():
        flat.extend(values)
    discoverer_order = {"leveldb": 0, "sqlite": 1, "json_files": 2, "jsonl": 3, "plist": 4}
    return sorted(flat, key=lambda candidate: (discoverer_order.get(candidate.discoverer, 99), candidate.path))


def _print_candidates(candidates: list[CandidateSource], *, store: ApprovalStore) -> None:
    if not candidates:
        click.echo("  (无)")
        click.echo("  共 0 个候选")
        return

    for candidate in candidates:
        candidate_id = store.candidate_id(candidate)
        status = _status_symbol(store.status(candidate_id))
        app_hint = candidate.app_hint or "?"
        click.echo(
            f"  [{status}] id={candidate_id} [{candidate.discoverer}/{candidate.confidence}] "
            f"{_display_path(candidate.path)} ({app_hint})"
        )
        if candidate.hint_tables:
            click.echo(f"    hint_tables: {', '.join(candidate.hint_tables)}")
        if candidate.hint_fields:
            click.echo(f"    hint_fields: {', '.join(candidate.hint_fields)}")
    click.echo(f"  共 {len(candidates)} 个候选")


def _load_candidates() -> list[CandidateSource]:
    discovered = discover_all()
    excluded = {
        instance.locator
        for instances in discovered.values()
        for instance in instances
    }
    return _flatten_candidates(discover_all_candidates(exclude_paths=excluded))


def _find_candidate_by_id(
    candidate_id: str,
    candidates: list[CandidateSource],
    *,
    store: ApprovalStore,
) -> CandidateSource | None:
    normalized_id = candidate_id.strip().lower()
    for candidate in candidates:
        if store.candidate_id(candidate) == normalized_id:
            return candidate
    return None


def _status_symbol(status: str) -> str:
    if status == "approved":
        return "✅"
    if status == "rejected":
        return "❌"
    return "新"


def _print_approval_records(records: list[ApprovalRecord], *, status_symbol: str, note_label: str) -> None:
    if not records:
        click.echo("  (无)")
        return
    for record in records:
        path = record.metadata.get("path", "")
        app_hint = record.metadata.get("app_hint") or "?"
        discoverer = record.metadata.get("discoverer", "?")
        ts = record.timestamp.isoformat() if record.timestamp else "-"
        line = (
            f"  [{status_symbol}] id={record.candidate_id} "
            f"[{discoverer}] {_display_path(path)} ({app_hint}) at={ts}"
        )
        if record.note:
            line += f" {note_label}={record.note}"
        click.echo(line)


def _display_path(path: str) -> str:
    try:
        expanded = Path(path).expanduser().resolve(strict=False)
        home = Path.home().resolve(strict=False)
    except Exception:
        return path

    try:
        rel = expanded.relative_to(home)
    except ValueError:
        return str(expanded)
    return "~/" + str(rel)
