from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, time, timezone
from pathlib import Path

import click

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

    click.echo("✅ 已识别（精读 plugins）：")
    if not recognized:
        click.echo("  (无)")
    for plugin, instances in recognized:
        click.echo(f"  {plugin.name}: {_recognized_summary(plugin.name, instances)}")

    click.echo("")
    click.echo("🟡 候选金矿（通用扫描，需用户确认）：")
    _print_candidates(candidates)

    click.echo("")
    click.echo("❌ 未发现：")
    if not missing:
        click.echo("  (无)")
    for name in missing:
        click.echo(f"  {name}")


@sources_group.command("candidates")
def candidates_command() -> None:
    discovered = discover_all()
    excluded = {
        instance.locator
        for instances in discovered.values()
        for instance in instances
    }
    candidates = _flatten_candidates(discover_all_candidates(exclude_paths=excluded))
    _print_candidates(candidates)


@sources_group.command("approve")
@click.argument("candidate_id")
def approve_command(candidate_id: str) -> None:
    _ = candidate_id
    click.echo("feature scheduled for S4")


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
    if len(instances) == 1:
        return _display_path(instances[0].locator)
    return f"{_display_path(instances[0].locator)} ... ({len(instances)} 个实例)"


def _flatten_candidates(candidates: dict[str, list[CandidateSource]]) -> list[CandidateSource]:
    flat: list[CandidateSource] = []
    for values in candidates.values():
        flat.extend(values)
    return sorted(flat, key=lambda candidate: (candidate.discoverer, candidate.path))


def _print_candidates(candidates: list[CandidateSource]) -> None:
    if not candidates:
        click.echo("  (无)")
        click.echo("  共 0 个候选")
        return

    for idx, candidate in enumerate(candidates, start=1):
        app_hint = candidate.app_hint or "?"
        click.echo(
            f"  [{idx}] [{candidate.discoverer}/{candidate.confidence}] "
            f"{_display_path(candidate.path)} ({app_hint})"
        )
        if candidate.hint_tables:
            hint_label = "hint_fields" if candidate.discoverer == "jsonl" else "hint_tables"
            click.echo(f"    {hint_label}: {', '.join(candidate.hint_tables)}")
    click.echo(f"  共 {len(candidates)} 个候选")


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
