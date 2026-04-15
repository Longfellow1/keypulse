from __future__ import annotations
import os
import sys
import time
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from keypulse.config import Config
from keypulse.store.db import init_db
from keypulse.store.repository import (
    get_sessions,
    get_session_by_id,
    query_raw_events,
    purge_raw_events,
    get_state,
    set_state,
    get_all_policies,
    insert_policy,
    apply_retention,
)
from keypulse.store.models import Policy, RawEvent, SearchDoc
from keypulse.privacy.desensitizer import desensitize
from keypulse.utils.paths import get_data_dir, get_db_path, get_pid_path, get_log_path, get_config_path
from keypulse.utils.lock import SingleInstanceLock
from keypulse.utils.logging import setup_logging
from keypulse.app import start_daemon, daemonize, run
from keypulse.services.timeline import get_timeline_rows
from keypulse.services.stats import get_stats
from keypulse.services.export import export_json, export_csv, export_markdown
from keypulse.services.sessionizer import sessions_for_today, recent_sessions
from keypulse.search.engine import search, recent_clipboard, recent_manual, recent_sessions_docs
from keypulse.capture.normalizer import normalize_manual_event


# Shared console objects
console = Console()
err_console = Console(stderr=True)


def get_config() -> Config:
    """Load config from standard locations."""
    return Config.load()


def require_db(cfg: Config):
    """Initialize database if not already done."""
    init_db(cfg.db_path_expanded)


@click.group()
def main():
    """KeyPulse — macOS personal activity monitoring CLI."""
    pass


# ═════════════════════════════════════════════════════════════════════════════
# 1. START
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--config", "config_path", default=None, help="Path to config.toml")
def start(config_path):
    """Start the KeyPulse daemon."""
    cfg = Config.load() if not config_path else _load_config_from(config_path)
    lock = SingleInstanceLock()

    if lock.is_running():
        err_console.print(f"[red]Already running (PID {lock.get_pid()})[/red]")
        sys.exit(1)

    # Init DB in parent so errors surface here
    require_db(cfg)

    # Warn about Accessibility permission before forking (visible to user)
    from keypulse.app import _check_accessibility
    if not _check_accessibility():
        console.print(
            "[yellow]⚠  Accessibility permission not granted.[/yellow]\n"
            "   Window titles won't be captured until you allow access:\n"
            "   System Settings → Privacy & Security → Accessibility → KeyPulse\n"
            "   Run [bold]keypulse doctor[/bold] to recheck."
        )

    # Fork to daemon
    pid = os.fork()
    if pid == 0:
        # Child process: become daemon
        daemonize(get_pid_path())
        run(cfg)
        sys.exit(0)
    else:
        # Parent process: wait briefly then report
        time.sleep(0.5)
        daemon_pid = lock.get_pid()
        if daemon_pid:
            console.print(f"[green]KeyPulse started (PID {daemon_pid})[/green]")
        else:
            console.print("[green]KeyPulse started[/green]")


def _load_config_from(path: str) -> Config:
    """Load config from explicit path."""
    import tomllib
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)


# ═════════════════════════════════════════════════════════════════════════════
# 2. STOP
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
def stop():
    """Stop the KeyPulse daemon."""
    lock = SingleInstanceLock()
    pid = lock.get_pid()

    if not pid:
        console.print("[yellow]KeyPulse is not running.[/yellow]")
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        console.print("[yellow]KeyPulse is not running.[/yellow]")
        return

    # Wait up to 5 seconds for process to exit
    for _ in range(50):
        time.sleep(0.1)
        if not lock.is_running():
            console.print("[green]KeyPulse stopped.[/green]")
            return

    console.print("[yellow]Stop signal sent (may still be shutting down).[/yellow]")


# ═════════════════════════════════════════════════════════════════════════════
# 3. PAUSE
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
def pause():
    """Pause activity monitoring."""
    cfg = get_config()
    require_db(cfg)
    set_state("status", "paused")
    console.print("[yellow]Monitoring paused[/yellow]")


# ═════════════════════════════════════════════════════════════════════════════
# 4. RESUME
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
def resume():
    """Resume activity monitoring."""
    cfg = get_config()
    require_db(cfg)
    set_state("status", "running")
    console.print("[green]Monitoring resumed[/green]")


# ═════════════════════════════════════════════════════════════════════════════
# 5. STATUS
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def status(plain):
    """Show daemon status."""
    cfg = get_config()
    require_db(cfg)

    lock = SingleInstanceLock()
    pid = lock.get_pid()
    is_running = pid is not None
    status_val = get_state("status") or "unknown"
    started_at = get_state("started_at") or "—"
    last_flush = get_state("last_flush") or "—"

    # DB size
    db_path = cfg.db_path_expanded
    db_size = db_path.stat().st_size if db_path.exists() else 0
    db_size_mb = db_size / (1024 * 1024)

    # Enabled watchers
    enabled = []
    if cfg.watchers.window:
        enabled.append("window")
    if cfg.watchers.idle:
        enabled.append("idle")
    if cfg.watchers.clipboard:
        enabled.append("clipboard")
    if cfg.watchers.manual:
        enabled.append("manual")
    if cfg.watchers.browser:
        enabled.append("browser")

    if plain:
        print(f"running={is_running}")
        print(f"pid={pid or 'none'}")
        print(f"status={status_val}")
        print(f"started_at={started_at}")
        print(f"db_path={db_path}")
        print(f"db_size_mb={db_size_mb:.2f}")
        print(f"last_flush={last_flush}")
        print(f"enabled_watchers={','.join(enabled)}")
    else:
        table = Table(show_header=False, box=None)
        table.add_row("Running", "[green]yes[/green]" if is_running else "[red]no[/red]")
        if is_running:
            table.add_row("PID", str(pid))
        table.add_row("Status", status_val)
        table.add_row("Started at", started_at)
        table.add_row("DB path", str(db_path))
        table.add_row("DB size", f"{db_size_mb:.2f} MB")
        table.add_row("Last flush", last_flush)
        table.add_row("Enabled watchers", ", ".join(enabled))
        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 6. DOCTOR
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def doctor(plain):
    """Check system configuration."""
    checks = {}

    # Python version
    import sys as sys_module
    checks["Python >= 3.11"] = sys_module.version_info >= (3, 11)

    # pyobjc-framework-AppKit
    try:
        import AppKit
        checks["pyobjc-framework-AppKit"] = True
    except ImportError:
        checks["pyobjc-framework-AppKit"] = False

    # pyobjc-framework-Quartz
    try:
        import Quartz
        checks["pyobjc-framework-Quartz"] = True
    except ImportError:
        checks["pyobjc-framework-Quartz"] = False

    # Accessibility permission
    try:
        from ApplicationServices import AXIsProcessTrusted
        checks["Accessibility permission"] = AXIsProcessTrusted()
    except Exception:
        checks["Accessibility permission"] = False

    # DB path writable
    cfg = get_config()
    db_path = cfg.db_path_expanded
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        test_file = db_path.parent / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        checks["DB path writable"] = True
    except Exception:
        checks["DB path writable"] = False

    # Config path exists
    config_path = get_config_path()
    checks["Config file exists"] = config_path.exists()

    if plain:
        for check_name, passed in checks.items():
            status = "OK" if passed else "FAIL"
            print(f"{check_name}: {status}")
    else:
        table = Table(title="System Check", show_header=True, header_style="bold cyan")
        table.add_column("Check")
        table.add_column("Status")
        for check_name, passed in checks.items():
            status = "[green]✓[/green]" if passed else "[red]✗[/red]"
            table.add_row(check_name, status)
        console.print(table)

    # Exit with error if any check failed
    if not all(checks.values()):
        sys.exit(1)


# ═════════════════════════════════════════════════════════════════════════════
# 7. SAVE
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--text", default=None, help="Text to save")
@click.option("--tag", default=None, help="Tag for the note")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def save(text, tag, plain):
    """Save a manual note."""
    # Read from stdin if no --text provided
    if text is None:
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            err_console.print("[red]No text provided. Use --text or pipe via stdin.[/red]")
            sys.exit(1)

    if not text or not text.strip():
        err_console.print("[red]Cannot save empty text.[/red]")
        sys.exit(1)

    cfg = get_config()
    require_db(cfg)

    # Normalize and insert
    from keypulse.store.repository import insert_raw_event

    event = normalize_manual_event(text.strip(), tags=tag)
    event_id = insert_raw_event(event)

    # Also insert search doc
    doc = SearchDoc(
        ref_type="manual",
        ref_id=str(event_id),
        title=text[:100] if text else "Note",
        body=text,
        tags=tag,
        app_name=None,
    )
    from keypulse.store.repository import insert_search_doc
    insert_search_doc(doc)

    if not plain:
        console.print("[green]Saved.[/green]")
    else:
        print("saved")


# ═════════════════════════════════════════════════════════════════════════════
# 8. TIMELINE
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--date", default=None, help="Date in YYYY-MM-DD format")
@click.option("--today", is_flag=True, default=False, help="Show today's timeline")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def timeline(date, today, plain):
    """Show activity timeline."""
    cfg = get_config()
    require_db(cfg)

    # Determine which date to use
    if date is None and today:
        date_str = None  # Will default to today
    elif date:
        date_str = date
    else:
        date_str = None  # Default to today

    rows = get_timeline_rows(date_str)

    if plain:
        for row in rows:
            print(f"{row['start']}\t{row['end']}\t{row['app']}\t{row['title']}\t{row['duration']}")
    else:
        if not rows:
            console.print("[yellow]No activities found.[/yellow]")
            return

        table = Table(title="Activity Timeline", show_header=True, header_style="bold cyan")
        table.add_column("Start")
        table.add_column("End")
        table.add_column("App")
        table.add_column("Title")
        table.add_column("Duration")

        for row in rows:
            table.add_row(
                row["start"],
                row["end"],
                row["app"],
                row["title"],
                row["duration"],
            )
        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 9. RECENT
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--type", "item_type", default=None, type=click.Choice(["clipboard", "manual", "session"]),
              help="Filter by type")
@click.option("--limit", default=20, help="Number of items to show")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def recent(item_type, limit, plain):
    """Show recent items."""
    cfg = get_config()
    require_db(cfg)

    items = []

    if item_type is None or item_type == "clipboard":
        items.extend([(item, "clipboard") for item in recent_clipboard(limit)])
    if item_type is None or item_type == "manual":
        items.extend([(item, "manual") for item in recent_manual(limit)])
    if item_type is None or item_type == "session":
        items.extend([(item, "session") for item in recent_sessions_docs(limit)])

    # Sort by created_at descending
    items.sort(key=lambda x: x[0].get("created_at") or x[0].get("started_at", ""), reverse=True)
    items = items[:limit]

    if plain:
        for item, item_type_val in items:
            title = item.get("title") or item.get("app_name") or "—"
            body = item.get("body", "")[:50] if item.get("body") else ""
            ts = item.get("created_at") or item.get("started_at", "—")
            print(f"{ts}\t{item_type_val}\t{title}\t{body}")
    else:
        if not items:
            console.print("[yellow]No recent items found.[/yellow]")
            return

        table = Table(title="Recent Items", show_header=True, header_style="bold cyan")
        table.add_column("Time")
        table.add_column("Type")
        table.add_column("Title/App")
        table.add_column("Body")

        for item, item_type_val in items:
            title = item.get("title") or item.get("app_name") or "—"
            body = item.get("body", "")[:80] if item.get("body") else ""
            ts = item.get("created_at") or item.get("started_at", "—")

            # Format timestamp nicely
            try:
                dt = datetime.fromisoformat(ts)
                ts_fmt = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_fmt = ts

            table.add_row(ts_fmt, item_type_val, title, body)

        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 10. STATS
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--days", default=7, help="Number of days to analyze")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def stats(days, plain):
    """Show activity statistics."""
    cfg = get_config()
    require_db(cfg)

    stats_data = get_stats(days)

    if plain:
        print(f"days={stats_data['days']}")
        print(f"total_sessions={stats_data['total_sessions']}")
        print(f"total_active_secs={stats_data['total_active_secs']}")
        print(f"total_active_human={stats_data['total_active_human']}")
        print(f"active_days={stats_data['active_days']}")
        print(f"clipboard_count={stats_data['clipboard_count']}")
        print(f"manual_count={stats_data['manual_count']}")
        for app in stats_data['app_distribution']:
            h = app['duration_sec'] // 3600
            m = (app['duration_sec'] % 3600) // 60
            print(f"app={app['app']},duration={h}h{m}m")
    else:
        # Summary panel
        summary_text = f"""
Total Sessions: {stats_data['total_sessions']}
Active Time: {stats_data['total_active_human']}
Active Days: {stats_data['active_days']}
Clipboard Events: {stats_data['clipboard_count']}
Manual Saves: {stats_data['manual_count']}
        """.strip()
        console.print(Panel(summary_text, title=f"Activity Stats — Last {days} days", expand=False))

        # App distribution
        if stats_data['app_distribution']:
            table = Table(title="Top Apps by Duration", show_header=True, header_style="bold cyan")
            table.add_column("App")
            table.add_column("Duration")
            for app in stats_data['app_distribution']:
                h = app['duration_sec'] // 3600
                m = (app['duration_sec'] % 3600) // 60
                table.add_row(app['app'], f"{h}h {m}m")
            console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 11. SEARCH
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.argument("query")
@click.option("--app", default=None, help="Filter by app name")
@click.option("--since", default=None, help="Time filter (7d, 24h, or YYYY-MM-DD)")
@click.option("--source", default=None, help="Filter by source (clipboard, manual, session)")
@click.option("--limit", default=50, help="Number of results")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def search_cmd(query, app, since, source, limit, plain):
    """Search activity."""
    cfg = get_config()
    require_db(cfg)

    results = search(query, app_name=app, since=since, source=source, limit=limit)

    if plain:
        for result in results:
            body = result.get("body", "")[:80] if result.get("body") else ""
            title = result.get("title", "")[:80] if result.get("title") else ""
            ts = result.get("created_at", "—")
            ref_type = result.get("ref_type", "—")
            app_name = result.get("app_name", "—")
            print(f"{ts}\t{ref_type}\t{app_name}\t{title}\t{body}")
    else:
        if not results:
            console.print("[yellow]No results found.[/yellow]")
            return

        table = Table(title=f"Search Results for '{query}'", show_header=True, header_style="bold cyan")
        table.add_column("Time")
        table.add_column("Type")
        table.add_column("App")
        table.add_column("Title/Body")

        for result in results:
            body = result.get("body", "")[:80] if result.get("body") else ""
            title = result.get("title", "")[:80] if result.get("title") else ""
            content = body or title or "—"
            ts = result.get("created_at", "—")

            # Format timestamp
            try:
                dt = datetime.fromisoformat(ts)
                ts_fmt = dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_fmt = ts

            ref_type = result.get("ref_type", "—")
            app_name = result.get("app_name", "—")

            table.add_row(ts_fmt, ref_type, app_name, content)

        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 12. SESSION (subgroup)
# ═════════════════════════════════════════════════════════════════════════════

@main.group()
def session():
    """Manage sessions."""
    pass


@session.command("list")
@click.option("--date", default=None, help="Date in YYYY-MM-DD format")
@click.option("--limit", default=100, help="Number of sessions to show")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def session_list(date, limit, plain):
    """List sessions."""
    cfg = get_config()
    require_db(cfg)

    sessions = get_sessions(date_str=date, limit=limit)

    if plain:
        for s in sessions:
            session_id = s["id"][:8] if s["id"] else "—"
            start = s.get("started_at", "—")
            end = s.get("ended_at", "—")
            app = s.get("app_name", "—")
            title = (s.get("primary_window_title") or "")[:60]
            duration = s.get("duration_sec", 0)
            print(f"{session_id}\t{start}\t{end}\t{app}\t{title}\t{duration}")
    else:
        if not sessions:
            console.print("[yellow]No sessions found.[/yellow]")
            return

        table = Table(title="Sessions", show_header=True, header_style="bold cyan")
        table.add_column("ID")
        table.add_column("Start")
        table.add_column("End")
        table.add_column("App")
        table.add_column("Title")
        table.add_column("Duration")

        for s in sessions:
            session_id = s["id"][:8] if s["id"] else "—"
            start = s.get("started_at", "—")
            end = s.get("ended_at", "—")
            app = s.get("app_name", "—")
            title = (s.get("primary_window_title") or "")[:60]
            duration = f"{s.get('duration_sec', 0)}s"

            # Format timestamps
            try:
                start_dt = datetime.fromisoformat(start)
                start = start_dt.astimezone().strftime("%H:%M:%S")
            except Exception:
                pass

            try:
                end_dt = datetime.fromisoformat(end)
                end = end_dt.astimezone().strftime("%H:%M:%S")
            except Exception:
                pass

            table.add_row(session_id, start, end, app, title, duration)

        console.print(table)


@session.command("show")
@click.argument("session_id")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def session_show(session_id, plain):
    """Show details of a session."""
    cfg = get_config()
    require_db(cfg)

    session_data = get_session_by_id(session_id)
    if not session_data:
        err_console.print(f"[red]Session not found: {session_id}[/red]")
        sys.exit(1)

    if plain:
        for key, value in session_data.items():
            print(f"{key}={value}")
    else:
        table = Table(show_header=False, box=None)
        for key, value in session_data.items():
            table.add_row(str(key), str(value))
        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 13. EXPORT
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--format", type=click.Choice(["json", "csv", "md"]), default="json",
              help="Export format")
@click.option("--days", default=None, type=int, help="Number of days to export")
@click.option("--date", default=None, help="Specific date in YYYY-MM-DD format")
@click.option("--output", default=None, help="Output file path")
def export(format, days, date, output):
    """Export activity data."""
    cfg = get_config()
    require_db(cfg)

    if format == "json":
        data = export_json(days=days, date_str=date)
    elif format == "csv":
        data = export_csv(days=days, date_str=date)
    elif format == "md":
        data = export_markdown(days=days, date_str=date)
    else:
        err_console.print(f"[red]Unknown format: {format}[/red]")
        sys.exit(1)

    if output:
        Path(output).write_text(data)
        console.print(f"[green]Exported to {output}[/green]")
    else:
        print(data)


# ═════════════════════════════════════════════════════════════════════════════
# 14. PURGE
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--today", is_flag=True, default=False, help="Purge today's data")
@click.option("--last-hours", type=int, default=None, help="Purge last N hours")
@click.option("--app", default=None, help="Purge data for specific app")
@click.option("--confirm", is_flag=True, default=False, help="Confirm deletion without prompt")
def purge(today, last_hours, app, confirm):
    """Delete activity data."""
    cfg = get_config()
    require_db(cfg)

    # Compute since/until
    now = datetime.now(timezone.utc)

    if today:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        until = now.isoformat()
    elif last_hours is not None:
        since = (now - timedelta(hours=last_hours)).isoformat()
        until = now.isoformat()
    else:
        err_console.print("[red]Specify --today or --last-hours[/red]")
        sys.exit(1)

    # Count what would be deleted
    from keypulse.store.db import get_conn
    conn = get_conn()
    clauses = ["ts_start >= ?"]
    params = [since]

    if until:
        clauses.append("ts_start <= ?")
        params.append(until)
    if app:
        clauses.append("app_name LIKE ?")
        params.append(f"%{app}%")

    where = "WHERE " + " AND ".join(clauses)
    count = conn.execute(f"SELECT COUNT(*) FROM raw_events {where}", params).fetchone()[0]

    if count == 0:
        console.print("[yellow]No data found to delete.[/yellow]")
        return

    # Prompt if not confirmed
    if not confirm:
        msg = f"Delete {count} events"
        if app:
            msg += f" from {app}"
        msg += "?"
        if not click.confirm(msg):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    # Delete
    purge_raw_events(since=since, until=until, app_name=app)

    # Also delete associated sessions and search docs
    conn.execute(
        f"DELETE FROM sessions WHERE started_at >= ? {'AND started_at <= ?' if until else ''}",
        (since, until) if until else (since,)
    )
    conn.execute(
        f"DELETE FROM search_docs WHERE created_at >= ? {'AND created_at <= ?' if until else ''}",
        (since, until) if until else (since,)
    )
    conn.commit()

    console.print(f"[green]Deleted {count} events.[/green]")


# ═════════════════════════════════════════════════════════════════════════════
# 15. CONFIG (subgroup)
# ═════════════════════════════════════════════════════════════════════════════

@main.group(name="config")
def config_group():
    """Manage configuration."""
    pass


@config_group.command("show")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def config_show(plain):
    """Show current configuration."""
    cfg = get_config()

    if plain:
        print(f"db_path={cfg.app.db_path}")
        print(f"log_path={cfg.app.log_path}")
        print(f"flush_interval_sec={cfg.app.flush_interval_sec}")
        print(f"retention_days={cfg.app.retention_days}")
        print(f"watchers_window={cfg.watchers.window}")
        print(f"watchers_idle={cfg.watchers.idle}")
        print(f"watchers_clipboard={cfg.watchers.clipboard}")
        print(f"watchers_manual={cfg.watchers.manual}")
        print(f"watchers_browser={cfg.watchers.browser}")
        print(f"idle_threshold_sec={cfg.idle.threshold_sec}")
        print(f"clipboard_max_text_length={cfg.clipboard.max_text_length}")
        print(f"clipboard_dedup_window_sec={cfg.clipboard.dedup_window_sec}")
    else:
        config_dict = cfg.model_dump()
        import json
        console.print(json.dumps(config_dict, indent=2))


@config_group.command("path")
def config_path():
    """Show config file path."""
    path = get_config_path()
    print(str(path))


# ═════════════════════════════════════════════════════════════════════════════
# 16. RULES (subgroup)
# ═════════════════════════════════════════════════════════════════════════════

@main.group()
def rules():
    """Manage privacy policies."""
    pass


@rules.command("list")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def rules_list(plain):
    """List all privacy policies."""
    cfg = get_config()
    require_db(cfg)

    policies = get_all_policies()

    if plain:
        for p in policies:
            print(f"{p['id']}\t{p['scope_type']}\t{p['scope_value']}\t{p['mode']}\t{p['priority']}")
    else:
        if not policies:
            console.print("[yellow]No policies configured.[/yellow]")
            return

        table = Table(title="Privacy Policies", show_header=True, header_style="bold cyan")
        table.add_column("ID")
        table.add_column("Scope Type")
        table.add_column("Scope Value")
        table.add_column("Mode")
        table.add_column("Priority")

        for p in policies:
            table.add_row(
                str(p.get("id", "—")),
                p.get("scope_type", "—"),
                p.get("scope_value", "—"),
                p.get("mode", "—"),
                str(p.get("priority", "—")),
            )
        console.print(table)


@rules.command("add")
@click.option("--scope-type", required=True, help="Scope type (app, window, source, content)")
@click.option("--scope-value", required=True, help="Scope value (e.g., 'Safari', 'password')")
@click.option("--mode", required=True, help="Mode (allow, deny, metadata-only, redact, truncate)")
@click.option("--priority", type=int, default=100, help="Priority (lower = higher priority)")
def rules_add(scope_type, scope_value, mode, priority):
    """Add a new privacy policy."""
    cfg = get_config()
    require_db(cfg)

    policy = Policy(
        scope_type=scope_type,
        scope_value=scope_value,
        mode=mode,
        priority=priority,
    )

    policy_id = insert_policy(policy)
    console.print(f"[green]Policy added (ID: {policy_id})[/green]")


@rules.command("disable")
@click.argument("rule_id", type=int)
def rules_disable(rule_id):
    """Disable a privacy policy."""
    cfg = get_config()
    require_db(cfg)

    from keypulse.store.db import get_conn
    conn = get_conn()
    conn.execute("UPDATE policies SET enabled=0 WHERE id=?", (rule_id,))
    conn.commit()

    console.print(f"[green]Policy {rule_id} disabled.[/green]")


if __name__ == "__main__":
    main()
