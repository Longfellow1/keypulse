from __future__ import annotations
import sys
import click
from penpal.store.db import get_conn
from penpal.i18n import T
from penpal.utils.paths import get_data_dir
from . import main
from ._shared import console, err_console, get_config, require_db
from .legacy import clean_legacy_data_files


@main.group(help=T("维护与清理命令。", "Maintenance and cleanup commands."))
def maintenance():
    """Maintenance group."""
    pass


@maintenance.command(
    name="clean-legacy",
    help=T("仅清理 Penpal 数据目录内的旧版 PID 与日志文件。", "Remove old PID and log files from the Penpal data directory only."),
)
def maintenance_clean_legacy():
    """Remove only Penpal-owned KeyPulse-named runtime files from ~/.penpal."""
    try:
        removed = clean_legacy_data_files(get_data_dir())
    except OSError as exc:
        raise click.ClickException(T(
            f"无法清理遗留文件：{exc}",
            f"Could not clean legacy files: {exc}",
        )) from exc
    if removed:
        console.print(T(f"已清理 {len(removed)} 个遗留文件：", f"Removed {len(removed)} legacy files:"))
        for path in removed:
            console.print(f"  {path}")
    else:
        console.print(T("数据目录中没有可清理的遗留 PID 或日志文件。", "No legacy PID or log files found in the data directory."))
    console.print(T(
        "旧版 App、launchd plist 和 ~/.keypulse 目录不会由此命令移除。",
        "This command does not remove old apps, launchd plists, or the ~/.keypulse directory.",
    ))

@maintenance.command(name="scrub-secrets", help=T("扫描并脱敏数据库内的敏感信息。", "Scan and redact secrets from the database."))
@click.option("--dry-run", is_flag=True, default=True, help=T("预览变更但不应用（默认 true）", "Preview changes without applying (default: true)"))
@click.option("--apply", is_flag=True, default=False, help=T("应用脱敏（必须显式指定）", "Apply redaction (must be explicit)"))
def maintenance_scrub_secrets(dry_run, apply):
    """Scrub secrets."""
    cfg = get_config()
    require_db(cfg)

    from penpal.services.secret_scrub import ScrubModeError, scrub_secrets

    try:
        result = scrub_secrets(get_conn(), dry_run=dry_run, apply=apply)
    except ScrubModeError:
        err_console.print("[red]Cannot use both --dry-run and --apply together.[/red]")
        sys.exit(1)
    if result.defaulted_to_dry_run:
        err_console.print("[yellow]Defaulting to dry-run. Use --dry-run explicitly or add --apply to redact.[/yellow]")
    console.print("[cyan]Scanning raw_events for secrets...[/cyan]")
    if result.changes:
        console.print(f"[yellow]Found {len(result.changes)} events with secrets[/yellow]")
        if result.dry_run:
            console.print("[cyan]Preview (dry-run):[/cyan]")
            for change in result.changes[:5]:
                console.print(f"  ID {change.event_id}:")
                console.print(f"    Before: {change.before[:80]}")
                console.print(f"    After:  {change.after[:80]}")
            if len(result.changes) > 5:
                console.print(f"  ... and {len(result.changes) - 5} more")
        else:
            console.print(f"[green]Redacted {len(result.changes)} events in raw_events[/green]")
    else:
        console.print("[green]No secrets found in raw_events[/green]")

    if result.dry_run:
        console.print("\n[cyan]This was a dry-run. To apply redaction, run:[/cyan]")
        console.print("[bold]  penpal maintenance scrub-secrets --apply[/bold]")
