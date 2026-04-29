from __future__ import annotations
import json
import os
import re
import sys
import time
import signal
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from keypulse.config import Config
from keypulse.store.db import init_db, get_conn
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
from keypulse.utils.paths import get_hud_pid_path
from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.lock import SingleInstanceLock
from keypulse.utils.logging import setup_logging
from keypulse.app import start_daemon, daemonize, run
from keypulse.integrations import resolve_active_sink
from keypulse.services.timeline import get_timeline_rows
from keypulse.services.stats import get_stats
from keypulse.services.export import export_json, export_csv, export_markdown, export_obsidian
from keypulse.pipeline.triggers import should_trigger, record_trigger
from keypulse.services.sessionizer import sessions_for_today, recent_sessions
from keypulse.search.engine import search, recent_clipboard, recent_manual, recent_sessions_docs
from keypulse.capture.normalizer import normalize_manual_event
from keypulse.utils.dates import local_day_bounds, resolve_local_date
from keypulse.hud import run_hud
from keypulse.pipeline import (
    PipelineInputs,
    build_daily_draft,
    append_feedback_event,
    read_feedback_events,
    FeedbackEvent,
    build_pipeline_plan,
    LLMMode,
    load_model_gateway,
    record_theme_feedback,
    current_theme_profile,
)
from keypulse.pipeline.model import ModelGateway
from keypulse.pipeline.model_keychain import (
    KeychainCommandError,
    KeychainUnavailable,
    check_daemon_keychain_access,
    render_plist_advice,
    store_secret,
)
from keypulse.pipeline.things import build_things, render_things_report, things_as_json
from keypulse.search.backends import resolve_search_backend


# Shared console objects
console = Console()
err_console = Console(stderr=True)


def get_config() -> Config:
    """Load config from standard locations."""
    return Config.load()


def require_db(cfg: Config):
    """Initialize database if not already done."""
    init_db(cfg.db_path_expanded)


def _launchd_daemon_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.keypulse.daemon.plist"


def _launchd_label_loaded(label: str) -> bool:
    try:
        result = subprocess.run(["launchctl", "list"], check=False, capture_output=True, text=True)
    except Exception:
        return False
    return label in (result.stdout or "")


def _launchd_bootout(plist_path: Path) -> bool:
    commands = [
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)],
        ["launchctl", "unload", str(plist_path)],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except Exception:
            continue
        if result.returncode == 0:
            return True
    return False


def _launchd_bootstrap(plist_path: Path) -> bool:
    commands = [
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
        ["launchctl", "load", str(plist_path)],
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.keypulse.daemon"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except Exception:
            continue
        if result.returncode == 0:
            return True
    return False


def _load_capture_runtime_state() -> dict | None:
    raw = get_state("capture_runtime")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


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
    launchd_plist = _launchd_daemon_plist_path()

    if launchd_plist.exists():
        if _launchd_label_loaded("com.keypulse.daemon"):
            err_console.print(f"[yellow]Already running under launchd (PID {lock.get_pid() or 'unknown'}).[/yellow]")
            sys.exit(1)
        if _launchd_bootstrap(launchd_plist):
            time.sleep(0.5)
            console.print(f"[green]KeyPulse started via launchd (PID {lock.get_pid() or 'unknown'}).[/green]")
            return

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


@main.command()
@click.option("--config", "config_path", default=None, help="Path to config.toml")
def serve(config_path):
    """Run KeyPulse in the foreground under a supervisor such as launchd."""
    cfg = Config.load() if not config_path else _load_config_from(config_path)
    run(cfg)


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
    launchd_plist = _launchd_daemon_plist_path()

    if launchd_plist.exists() and _launchd_label_loaded("com.keypulse.daemon"):
        if _launchd_bootout(launchd_plist):
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for _ in range(50):
                time.sleep(0.1)
                if not lock.is_running():
                    console.print("[green]KeyPulse stopped and launchd supervision disabled.[/green]")
                    return
            console.print("[yellow]launchd 已卸载，但旧进程仍在退出中。[/yellow]")
            return
        console.print("[yellow]KeyPulse is supervised by launchd, but unload failed.[/yellow]")
        return

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
    supervised = _launchd_label_loaded("com.keypulse.daemon")
    status_val = get_state("status") or "unknown"
    started_at = get_state("started_at") or "—"
    last_flush = get_state("last_flush") or "—"
    runtime = _load_capture_runtime_state() or {}
    runtime_counts = runtime.get("multi_source_counts") or {}
    runtime_watchers = runtime.get("watchers") or {}
    runtime_pid = runtime.get("pid") or "unknown"
    runtime_host = runtime.get("host_executable") or "unknown"
    keyboard_source = ((runtime_watchers.get("keyboard_chunk") or {}).get("source") or {}).get("status") or "unknown"
    ax_running = bool((runtime_watchers.get("ax_text") or {}).get("running"))
    ocr_running = bool((runtime_watchers.get("ocr") or {}).get("running"))
    ax_count = int(runtime_counts.get("ax_text") or 0)
    ocr_count = int(runtime_counts.get("ocr_text") or 0)
    keyboard_count = int(runtime_counts.get("keyboard_chunk") or 0)

    # DB size
    db_path = cfg.db_path_expanded
    db_size = db_path.stat().st_size if db_path.exists() else 0
    db_size_mb = db_size / (1024 * 1024)

    # Enabled watchers
    enabled = []
    if cfg.watchers.window:
        enabled.append("窗口活动")
    if cfg.watchers.idle:
        enabled.append("空闲检测")
    if cfg.watchers.clipboard:
        enabled.append("剪贴板")
    if cfg.watchers.manual:
        enabled.append("手动保存")
    if cfg.watchers.browser:
        enabled.append("浏览器")
    if getattr(cfg.watchers, "ax_text", False):
        enabled.append("当前看到的正文")
    if getattr(cfg.watchers, "keyboard_chunk", False):
        enabled.append("键入整理片段")
    if getattr(cfg.watchers, "ocr", False):
        enabled.append("屏幕识别补充")

    if plain:
        print(f"running={is_running}")
        print(f"pid={pid or 'none'}")
        print(f"supervised_by_launchd={supervised}")
        print(f"status={status_val}")
        print(f"started_at={started_at}")
        print(f"db_path={db_path}")
        print(f"db_size_mb={db_size_mb:.2f}")
        print(f"last_flush={last_flush}")
        print(f"enabled_watchers={','.join(enabled)}")
        print(f"runtime_ax_running={ax_running}")
        print(f"runtime_ocr_running={ocr_running}")
        print(f"runtime_keyboard_source={keyboard_source}")
        print(f"runtime_pid={runtime_pid}")
        print(f"runtime_host={runtime_host}")
        print(f"runtime_ax_count={ax_count}")
        print(f"runtime_ocr_count={ocr_count}")
        print(f"runtime_keyboard_count={keyboard_count}")
    else:
        status_label = {
            "running": "运行中",
            "paused": "已暂停",
            "stopped": "已停止",
            "unknown": "未知",
        }.get(status_val, status_val)
        table = Table(title="采集状态", show_header=False, box=None)
        table.add_row("服务状态", "[green]运行中[/green]" if is_running else "[red]未运行[/red]")
        if is_running:
            table.add_row("进程 PID", str(pid))
        table.add_row("运行方式", "launchd 托管" if supervised else "手动启动")
        table.add_row("采集阶段", status_label)
        table.add_row("启动时间", started_at)
        table.add_row("数据库路径", str(db_path))
        table.add_row("数据库大小", f"{db_size_mb:.2f} MB")
        table.add_row("最近一次写入", last_flush)
        table.add_row("已启用采集源", "、".join(enabled) if enabled else "无")
        table.add_row("正文采集状态", "已运行" if ax_running else "未见运行")
        table.add_row("屏幕识别状态", "已运行" if ocr_running else "未见运行")
        table.add_row("键入整理状态", keyboard_source)
        table.add_row("后台宿主 PID", str(runtime_pid))
        table.add_row("后台宿主路径", str(runtime_host))
        table.add_row("正文采集条数", str(ax_count))
        table.add_row("屏幕识别条数", str(ocr_count))
        table.add_row("键入整理条数", str(keyboard_count))
        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 6. DOCTOR
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def doctor(plain):
    """Check system configuration."""
    checks = {}
    cfg = get_config()
    require_db(cfg)

    # Python version
    import sys as sys_module
    checks["Python >= 3.11"] = sys_module.version_info >= (3, 11)

    # Cocoa / AppKit
    try:
        import AppKit  # noqa: F401
        checks["macOS 原生界面能力"] = True
    except ImportError:
        checks["macOS 原生界面能力"] = False

    # Quartz
    try:
        import Quartz
        checks["图像与事件框架"] = True
    except ImportError:
        checks["图像与事件框架"] = False

    try:
        import Vision  # noqa: F401
        checks["本地屏幕识别能力"] = True
    except ImportError:
        checks["本地屏幕识别能力"] = False

    # Accessibility permission
    try:
        from ApplicationServices import AXIsProcessTrusted
        checks["辅助功能权限"] = AXIsProcessTrusted()
    except Exception:
        checks["辅助功能权限"] = False

    try:
        import Quartz
        preflight_listen = getattr(Quartz, "CGPreflightListenEventAccess", None)
        checks["键盘监听权限"] = bool(preflight_listen()) if callable(preflight_listen) else False
    except Exception:
        checks["键盘监听权限"] = False

    try:
        import Quartz
        preflight_screen = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
        checks["屏幕录制权限"] = bool(preflight_screen()) if callable(preflight_screen) else False
    except Exception:
        checks["屏幕录制权限"] = False

    # DB path writable
    db_path = cfg.db_path_expanded
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        test_file = db_path.parent / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        checks["数据库目录可写"] = True
    except Exception:
        checks["数据库目录可写"] = False

    # Config path exists
    config_path = get_config_path()
    checks["配置文件存在"] = config_path.exists()
    runtime = _load_capture_runtime_state() or {}
    runtime_watchers = runtime.get("watchers") or {}
    keyboard_source = ((runtime_watchers.get("keyboard_chunk") or {}).get("source") or {}).get("status")
    if runtime_watchers:
        checks["后台正文采集线程"] = bool((runtime_watchers.get("ax_text") or {}).get("running"))
        checks["后台屏幕识别线程"] = bool((runtime_watchers.get("ocr") or {}).get("running"))
        checks["后台键盘监听线程"] = keyboard_source == "running"

    if plain:
        for check_name, passed in checks.items():
            status = "OK" if passed else "FAIL"
            print(f"{check_name}: {status}")
    else:
        table = Table(title="系统健康检查", show_header=True, header_style="bold cyan")
        table.add_column("检查项")
        table.add_column("结果")
        for check_name, passed in checks.items():
            status = "[green]正常[/green]" if passed else "[red]未通过[/red]"
            table.add_row(check_name, status)
        console.print(table)
        if runtime_watchers and keyboard_source not in (None, "running"):
            console.print(
                Panel(
                    f"后台键盘监听当前状态：{keyboard_source}\n"
                    "如果你已经给终端授权，但后台 launchd 仍然拿不到正文/键入事件，"
                    "需要把 KeyPulse 实际宿主也加入辅助功能、键盘监听、屏幕录制。",
                    title="运行时提示",
                    border_style="yellow",
                )
            )

    # Exit with error if any check failed
    if not all(checks.values()):
        sys.exit(1)


@main.command()
@click.option("--config", "config_path", default=None)
def healthcheck(config_path):
    """Run health check and write ~/.keypulse/health.json."""
    from keypulse.health import run_healthcheck

    result = run_healthcheck(config_path=config_path)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    if result["overall"] == "alert" and any(alert["severity"] == "error" for alert in result["alerts"]):
        raise SystemExit(1)


def _hud_lock() -> SingleInstanceLock:
    return SingleInstanceLock(get_hud_pid_path())


def _find_legacy_hud_pid() -> int | None:
    try:
        result = subprocess.run(["pgrep", "-f", "keypulse hud"], check=False, capture_output=True, text=True)
    except Exception:
        return None

    current_pid = os.getpid()
    for line in (result.stdout or "").splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid == current_pid:
            continue
        try:
            command_result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            continue
        command = (command_result.stdout or "").strip()
        if not command:
            continue
        if "keypulse hud" not in command:
            continue
        if any(marker in command for marker in [" hud status", " hud stop", " hud close", " hud start"]):
            continue
        if command.rstrip().endswith("keypulse hud") or command.rstrip().endswith("keypulse.cli hud"):
            return pid
    return None


def _start_hud():
    cfg = get_config()
    lock = _hud_lock()
    if not lock.acquire():
        err_console.print(f"[yellow]HUD already running (PID {lock.get_pid()}).[/yellow]")
        sys.exit(1)
    legacy_pid = _find_legacy_hud_pid()
    if legacy_pid:
        lock.release()
        err_console.print(f"[yellow]HUD already running (PID {legacy_pid}).[/yellow]")
        sys.exit(1)

    require_db(cfg)
    try:
        run_hud(cfg)
    finally:
        lock.release()


def _stop_hud():
    lock = _hud_lock()
    pid = lock.get_pid() or _find_legacy_hud_pid()

    if not pid:
        console.print("[yellow]HUD is not running.[/yellow]")
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        console.print("[yellow]HUD is not running.[/yellow]")
        return

    for _ in range(50):
        time.sleep(0.1)
        if not lock.is_running():
            console.print("[green]HUD stopped.[/green]")
            return

    console.print("[yellow]Stop signal sent (HUD may still be shutting down).[/yellow]")


def _show_hud_status(plain: bool = False):
    lock = _hud_lock()
    pid = lock.get_pid() or _find_legacy_hud_pid()
    is_running = pid is not None

    if plain:
        print(f"running={is_running}")
        print(f"pid={pid or 'none'}")
        print(f"pid_path={lock.pid_path}")
        return

    table = Table(show_header=False, box=None)
    table.add_row("HUD 状态", "[green]运行中[/green]" if is_running else "[yellow]未运行[/yellow]")
    table.add_row("PID", str(pid or "—"))
    table.add_row("PID 文件", str(lock.pid_path))
    console.print(table)


@main.group(invoke_without_command=True)
@click.pass_context
def hud(ctx):
    """Manage the macOS status bar HUD."""
    if ctx.invoked_subcommand is None:
        _start_hud()


@hud.command("start")
def hud_start():
    """Launch the macOS status bar HUD."""
    _start_hud()


@hud.command("stop")
def hud_stop():
    """Stop the running HUD instance."""
    _stop_hud()


@hud.command("close")
def hud_close():
    """Close the running HUD instance."""
    _stop_hud()


@hud.command("status")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def hud_status(plain):
    """Show HUD process status."""
    _show_hud_status(plain=plain)


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

@main.command(name="search")
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

    backend = resolve_search_backend()
    results = backend.search(query, app_name=app, since=since, source=source, limit=limit)

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
# 13. OBSIDIAN
# ═════════════════════════════════════════════════════════════════════════════

@main.group()
def obsidian():
    """Manage the Obsidian export bridge."""
    pass


def _resolve_obsidian_date(date: Optional[str], yesterday: bool) -> str:
    return resolve_local_date(date, yesterday=yesterday)


def _sync_obsidian_bundle(
    cfg: Config,
    date_str: str,
    output: str | None = None,
    vault_name: str | None = None,
    *,
    incremental: bool = False,
) -> tuple[int, str, str]:
    sink = resolve_active_sink(cfg, persist=True)
    target_output = output or str(sink.output_dir)
    target_vault = vault_name or cfg.obsidian.vault_name
    gateway = load_model_gateway(cfg) if hasattr(cfg, "model") else None
    written = export_obsidian(
        target_output,
        date_str=date_str,
        vault_name=target_vault,
        model_gateway=gateway,
        incremental=incremental,
        db_path=str(cfg.db_path_expanded),
        use_narrative_v2=getattr(getattr(cfg, "pipeline", None), "use_narrative_v2", False),
        use_narrative_skeleton=getattr(getattr(cfg, "pipeline", None), "use_narrative_skeleton", False),
        use_things_narrative=getattr(getattr(cfg, "pipeline", None), "use_things_narrative", True),
        things_idle_threshold_minutes=getattr(
            getattr(cfg, "pipeline", None),
            "things_idle_threshold_minutes",
            30,
        ),
    )
    return len(written), target_output, sink.kind


# ═════════════════════════════════════════════════════════════════════════════
# 13.5 SINKS
# ═════════════════════════════════════════════════════════════════════════════

@main.group()
def sinks():
    """Manage automatic sink discovery."""
    pass


@sinks.command("detect")
@click.option("--apply", "apply_binding", is_flag=True, default=False, help="Persist the detected sink binding")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def sinks_detect(apply_binding, plain):
    """Detect the active local sink and optionally persist it."""
    cfg = get_config()
    sink = resolve_active_sink(cfg, persist=apply_binding)

    if plain:
        print(f"kind={sink.kind}")
        print(f"output_dir={sink.output_dir}")
        print(f"source={sink.source}")
    else:
        console.print(
            f"[green]{sink.kind}[/green] -> {sink.output_dir} "
            f"([dim]{sink.source}[/dim])"
        )


@sinks.command("status")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def sinks_status(plain):
    """Show the active sink binding."""
    cfg = get_config()
    sink = resolve_active_sink(cfg)

    if plain:
        print(f"kind={sink.kind}")
        print(f"output_dir={sink.output_dir}")
        print(f"source={sink.source}")
    else:
        table = Table(show_header=False, box=None)
        table.add_row("Kind", sink.kind)
        table.add_row("Output dir", str(sink.output_dir))
        table.add_row("Source", sink.source)
        console.print(table)


@obsidian.command("sync")
@click.option(
    "--incremental",
    is_flag=True,
    default=False,
    help="Incremental append mode: only add new events, do not re-render narrative.",
)
@click.option("--yesterday", is_flag=True, default=False, help="Export yesterday's data (full sync)")
@click.option("--date", default=None, help="Specific date in YYYY-MM-DD format (mutually exclusive with --incremental and --yesterday)")
@click.option("--output", default=None, help="Override vault path")
@click.option("--vault-name", default=None, help="Override vault name")
def obsidian_sync(date, yesterday, incremental, output, vault_name):
    """Export a daily Obsidian bundle."""
    cfg = get_config()
    require_db(cfg)

    selected_flags = int(bool(incremental)) + int(bool(yesterday)) + int(bool(date))
    if selected_flags > 1:
        raise click.UsageError("--incremental, --yesterday, and --date are mutually exclusive.")

    # T1 trigger gating: only run if activity ≥50 chars in last 5h (unless --yesterday/--date specified)
    if not yesterday and not date:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        allowed, reason = should_trigger("T1", now=now, db_path=cfg.db_path_expanded, cfg={})
        if not allowed:
            record_trigger("T1", now=now, db_path=cfg.db_path_expanded, outcome=f"skipped:{reason}")
            click.echo(f"[T1] skipped: {reason}")
            return
        record_trigger("T1", now=now, db_path=cfg.db_path_expanded, outcome="allowed")

    if incremental:
        date_str = resolve_local_date("today", yesterday=False)
    elif yesterday:
        date_str = _resolve_obsidian_date(None, yesterday=True)
    elif date:
        date_str = _resolve_obsidian_date(date, yesterday=False)
    else:
        date_str = _resolve_obsidian_date(None, yesterday=False)

    try:
        written, target_output, sink_kind = _sync_obsidian_bundle(
            cfg,
            date_str,
            output=output,
            vault_name=vault_name,
            incremental=incremental,
        )
        console.print(f"[green]Exported {written} notes to {target_output}[/green]")
        # Record successful run if T1 gating was used
        if not yesterday and not date:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            record_trigger("T1", now=now, db_path=cfg.db_path_expanded, outcome="ran:ok")
    except Exception as e:
        # Record failure if T1 gating was used
        if not yesterday and not date:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            record_trigger("T1", now=now, db_path=cfg.db_path_expanded, outcome="ran:fail", note=str(e))
        raise


# ═════════════════════════════════════════════════════════════════════════════
# 13.5 PIPELINE
# ═════════════════════════════════════════════════════════════════════════════

@main.group()
def pipeline():
    """Inspect and operate the information pipeline."""
    pass


@pipeline.command("sync")
@click.option("--date", default=None, help="Specific date in YYYY-MM-DD format")
@click.option("--yesterday", is_flag=True, default=False, help="Sync yesterday's data")
@click.option("--output", default=None, help="Override vault path")
@click.option("--vault-name", default=None, help="Override vault name")
def pipeline_sync(date, yesterday, output, vault_name):
    """Run the unified daily sync path."""
    cfg = get_config()
    require_db(cfg)

    date_str = _resolve_obsidian_date(date, yesterday)
    written, target_output, sink_kind = _sync_obsidian_bundle(cfg, date_str, output=output, vault_name=vault_name)
    print(f"pipeline_sync=ok date={date_str} sink={sink_kind} output={target_output} written={written}")


@pipeline.command("draft")
@click.option("--date", default=None, help="Specific date in YYYY-MM-DD format")
@click.option("--yesterday", is_flag=True, default=False, help="Build yesterday's draft")
@click.option("--output", default=None, help="Write the draft to a file instead of stdout")
def pipeline_draft(date, yesterday, output):
    """Build a deterministic daily draft from raw events."""
    cfg = get_config()
    require_db(cfg)

    date_str = _resolve_obsidian_date(date, yesterday)
    since, until = local_day_bounds(date_str)
    events = query_raw_events(since=since, until=until, limit=50000)
    inputs = PipelineInputs(
        event_count=len(events),
        candidate_count=0,
        topic_count=0,
        active_days=1,
    )
    llm_mode = getattr(cfg.pipeline, "llm_mode", "off")
    feedback_events = read_feedback_events(Path(cfg.pipeline.feedback_path).expanduser())
    draft = build_daily_draft(
        inputs,
        events,
        model_gateway=load_model_gateway(cfg) if hasattr(cfg, "model") else None,
        plan=build_pipeline_plan(LLMMode.OFF if llm_mode == "off" else LLMMode(llm_mode), inputs),
        feedback_events=feedback_events,
        use_narrative_v2=getattr(getattr(cfg, "pipeline", None), "use_narrative_v2", False),
        use_narrative_skeleton=getattr(getattr(cfg, "pipeline", None), "use_narrative_skeleton", False),
        db_path=cfg.db_path_expanded,
        date_str=date_str,
    )

    if output:
        Path(output).write_text(draft.body)
        console.print(f"[green]Draft written to {output}[/green]")
    else:
        console.print(draft.body)


def _parse_pipeline_bound(raw: str | None, *, is_since: bool) -> datetime:
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    now_local = datetime.now(local_tz)

    if raw is None:
        if is_since:
            return datetime.combine(now_local.date(), datetime.min.time(), tzinfo=local_tz).astimezone(timezone.utc)
        return now_local.astimezone(timezone.utc)

    if len(raw) == 10:
        day = datetime.fromisoformat(raw)
        if is_since:
            local_value = datetime.combine(day.date(), datetime.min.time(), tzinfo=local_tz)
        else:
            local_value = datetime.combine(day.date(), datetime.max.time(), tzinfo=local_tz)
        return local_value.astimezone(timezone.utc)

    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=local_tz)
    return parsed.astimezone(timezone.utc)


@pipeline.command("things")
@click.option("--since", default=None)
@click.option("--until", default=None)
@click.option("--source", "sources", multiple=True, help="可多次指定限制源")
@click.option("--no-llm", is_flag=True, default=False, help="不调 LLM，走 fallback")
@click.option("--idle-threshold", default=30, type=int, show_default=True, help="session 空闲切分阈值（分钟）")
@click.option("--json", "as_json", is_flag=True, default=False)
def pipeline_things(since, until, sources, no_llm, idle_threshold, as_json):
    """聚类 SemanticEvent 为'事情'并描述"""
    cfg = get_config()
    require_db(cfg)

    since_dt = _parse_pipeline_bound(since, is_since=True)
    until_dt = _parse_pipeline_bound(until, is_since=False)
    if until_dt < since_dt:
        raise click.UsageError("until must be >= since")

    gateway = None
    if not no_llm and hasattr(cfg, "model"):
        gateway = load_model_gateway(cfg)

    thing_list = build_things(
        since_dt,
        until_dt,
        model_gateway=gateway,
        sources=list(sources) if sources else None,
        idle_threshold_minutes=idle_threshold,
    )

    if as_json:
        print(things_as_json(thing_list))
        return
    print(render_things_report(thing_list, model_gateway=gateway))


@pipeline.group()
def feedback():
    """Record and inspect pipeline feedback."""
    pass


@feedback.command("add")
@click.option("--kind", required=True, help="Feedback kind, such as promote or demote")
@click.option("--target", required=True, help="Target topic, event, or draft")
@click.option("--note", required=True, help="Short feedback note")
def feedback_add(kind, target, note):
    """Append one feedback event to the local feedback log."""
    cfg = get_config()
    path = Path(cfg.pipeline.feedback_path).expanduser()
    append_feedback_event(path, FeedbackEvent(kind=kind, target=target, note=note))
    console.print(f"[green]Recorded feedback for {target}[/green]")


@feedback.command("list")
@click.option("--path", default=None, help="Override feedback log path")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def feedback_list(path, plain):
    """List recorded feedback events."""
    cfg = get_config()
    feedback_path = Path(path or cfg.pipeline.feedback_path).expanduser()
    events = read_feedback_events(feedback_path)

    if plain:
        for event in events:
            print(f"{event.created_at}\t{event.kind}\t{event.target}\t{event.note}")
    else:
        table = Table(show_header=True, box=None)
        table.add_column("Created at")
        table.add_column("Kind")
        table.add_column("Target")
        table.add_column("Note")
        for event in events:
            table.add_row(event.created_at, event.kind, event.target, event.note)
        console.print(table)


@feedback.command("refine")
@click.option("--theme", "theme_name", required=True, help="Theme name to refine")
@click.option("--instruction", required=True, help="Refinement instruction")
@click.option("--state-path", default=None, help="Override theme state path")
def feedback_refine(theme_name, instruction, state_path):
    """Persist a theme refinement instruction."""
    result = record_theme_feedback(state_path, theme_name=theme_name, instruction=instruction)
    console.print(f"[green]{result['theme_name']} v{result['version']}[/green]")


@feedback.command("status")
@click.option("--state-path", default=None, help="Override theme state path")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def feedback_status(state_path, plain):
    """Show the active theme profile."""
    profile = current_theme_profile(state_path)
    if plain:
        print(f"theme_name={profile['theme_name']}")
        print(f"version={profile['version']}")
        print(f"instructions={'|'.join(profile['instructions'])}")
        print(f"updated_at={profile['updated_at']}")
    else:
        table = Table(show_header=False, box=None)
        table.add_row("Theme", f"{profile['theme_name']} v{profile['version']}")
        table.add_row("Instructions", ", ".join(profile["instructions"]))
        table.add_row("Updated at", str(profile["updated_at"]))
        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 14. EXPORT
# ═════════════════════════════════════════════════════════════════════════════

@main.command()
@click.option("--format", type=click.Choice(["json", "csv", "md", "obsidian"]), default="json",
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
    elif format == "obsidian":
        if output:
            target_output = output
        else:
            sink = resolve_active_sink(cfg, persist=True)
            target_output = str(sink.output_dir)
        gateway = load_model_gateway(cfg) if hasattr(cfg, "model") else None
        written = export_obsidian(
            output_dir=target_output,
            days=days,
            date_str=date,
            vault_name=cfg.obsidian.vault_name,
            model_gateway=gateway,
            use_narrative_skeleton=getattr(getattr(cfg, "pipeline", None), "use_narrative_skeleton", False),
            use_things_narrative=getattr(getattr(cfg, "pipeline", None), "use_things_narrative", True),
            things_idle_threshold_minutes=getattr(
                getattr(cfg, "pipeline", None),
                "things_idle_threshold_minutes",
                30,
            ),
        )
        console.print(f"[green]Exported {len(written)} notes to {target_output}[/green]")
        return
    else:
        err_console.print(f"[red]Unknown format: {format}[/red]")
        sys.exit(1)

    if output:
        Path(output).write_text(data)
        console.print(f"[green]Exported to {output}[/green]")
    else:
        print(data)


# ═════════════════════════════════════════════════════════════════════════════
# 15. PURGE
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
# 16. CONFIG (subgroup)
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
        print(f"obsidian_vault_path={cfg.obsidian.vault_path}")
        print(f"obsidian_vault_name={cfg.obsidian.vault_name}")
        print(f"obsidian_export_hour={cfg.obsidian.export_hour}")
        print(f"obsidian_export_minute={cfg.obsidian.export_minute}")
        print(f"pipeline_llm_mode={cfg.pipeline.llm_mode}")
        print(f"pipeline_max_llm_calls_per_run={cfg.pipeline.max_llm_calls_per_run}")
        print(f"pipeline_max_llm_input_chars_per_run={cfg.pipeline.max_llm_input_chars_per_run}")
        print(f"pipeline_feedback_path={cfg.pipeline.feedback_path}")
        print(f"integration_standalone_output_path={cfg.integration.standalone_output_path}")
        print(f"integration_state_path={cfg.integration.state_path}")
        print(f"model_active_profile={cfg.model.active_profile}")
        print(f"model_state_path={cfg.model.state_path}")
        print(f"model_local_kind={cfg.model.local.kind}")
        print(f"model_local_base_url={cfg.model.local.base_url}")
        print(f"model_local_model={cfg.model.local.model}")
        print(f"model_cloud_kind={cfg.model.cloud.kind}")
        print(f"model_cloud_base_url={cfg.model.cloud.base_url}")
        print(f"model_cloud_model={cfg.model.cloud.model}")
        print(f"model_cloud_api_key_source={getattr(cfg.model.cloud, 'api_key_source', '')}")
        print(f"model_cloud_api_key_env={cfg.model.cloud.api_key_env}")
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
# ═════════════════════════════════════════════════════════════════════════════
# 17. MODEL (subgroup)
# ═════════════════════════════════════════════════════════════════════════════

@main.group()
def model():
    """Manage model gateway profiles."""
    pass


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("\"", "\\\"")
    return f"\"{escaped}\""


def _render_model_section(model_payload: dict[str, object]) -> str:
    local = model_payload["local"]
    cloud = model_payload["cloud"]
    return "\n".join(
        [
            "[model]",
            f"active_profile = {_toml_quote(str(model_payload['active_profile']))}",
            f"state_path = {_toml_quote(str(model_payload['state_path']))}",
            "",
            "[model.local]",
            f"kind = {_toml_quote(str(local['kind']))}",
            f"base_url = {_toml_quote(str(local['base_url']))}",
            f"model = {_toml_quote(str(local['model']))}",
            f"timeout_sec = {int(local['timeout_sec'])}",
            "",
            "[model.cloud]",
            f"kind = {_toml_quote(str(cloud['kind']))}",
            f"base_url = {_toml_quote(str(cloud['base_url']))}",
            f"model = {_toml_quote(str(cloud['model']))}",
            f"api_key_source = {_toml_quote(str(cloud['api_key_source']))}",
            f"api_key_env = {_toml_quote(str(cloud['api_key_env']))}",
            f"timeout_sec = {int(cloud['timeout_sec'])}",
        ]
    )


def _replace_model_tables(existing_text: str, model_section: str) -> str:
    if not existing_text.strip():
        return model_section.strip() + "\n"

    kept_lines: list[str] = []
    skipping = False
    table_re = re.compile(r"^\s*\[([^\]]+)\]\s*$")

    for line in existing_text.splitlines(keepends=True):
        match = table_re.match(line)
        if match:
            name = match.group(1).strip()
            is_model_table = name == "model" or name.startswith("model.")
            if is_model_table:
                skipping = True
                continue
            if skipping:
                skipping = False
        if not skipping:
            kept_lines.append(line)

    kept = "".join(kept_lines).rstrip()
    if kept:
        return f"{kept}\n\n{model_section.strip()}\n"
    return model_section.strip() + "\n"


def _write_model_config_atomic(path: Path, model_payload: dict[str, object]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    new_text = _replace_model_tables(existing, _render_model_section(model_payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, new_text)


def _probe_backend(
    *,
    kind: str,
    base_url: str,
    model: str,
    api_key: str = "",
    timeout_sec: int = 20,
) -> tuple[bool, str, float]:
    started = time.perf_counter()
    normalized_base = base_url.strip().rstrip("/")
    headers = {"Content-Type": "application/json"}
    if kind == "openai_compatible" and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    if kind == "ollama":
        path = "/api/chat"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with ok."}],
            "stream": False,
        }
    else:
        path = "/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with ok."}],
            "temperature": 0,
        }

    if re.search(r"/v\d+$", normalized_base) and path.startswith("/v1/"):
        path = path.removeprefix("/v1")

    url = f"{normalized_base}{path}"
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_sec) as response:
            body = json.loads(response.read().decode("utf-8"))
        if kind == "ollama":
            content = str((body.get("message") or {}).get("content") or "").strip()
        else:
            choices = body.get("choices") or []
            content = str(((choices[0].get("message") or {}).get("content")) if choices else "").strip()
        elapsed = time.perf_counter() - started
        if content:
            return True, "OK", elapsed
        return False, "empty response", elapsed
    except HTTPError as exc:
        elapsed = time.perf_counter() - started
        return False, f"HTTP {exc.code}", elapsed
    except Exception as exc:  # pragma: no cover - network boundary
        elapsed = time.perf_counter() - started
        return False, str(exc), elapsed


def _relative_time_text(ts_value: str) -> str:
    try:
        parsed = datetime.fromisoformat(ts_value)
    except ValueError:
        return "unknown"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    seconds = int(max(delta.total_seconds(), 0))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def _short_circuit_text(entry: object) -> str:
    if not isinstance(entry, dict):
        return "0 / 0 min cooldown"
    reason = str(entry.get("reason") or "")
    cooldown = 30 if reason == "auth" else 5
    until_text = str(entry.get("until") or "")
    try:
        until = datetime.fromisoformat(until_text)
    except ValueError:
        return f"0 / {cooldown} min cooldown"
    if until.tzinfo is None or until.utcoffset() is None:
        until = until.replace(tzinfo=timezone.utc)
    remaining = max((until.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds(), 0)
    remaining_min = int(remaining // 60) if remaining > 0 else 0
    return f"{remaining_min} / {cooldown} min cooldown"


@model.command("status")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def model_status(plain):
    """Show model profile, backend health, and fallback state."""
    cfg = get_config()
    gateway = load_model_gateway(cfg)
    snapshot = gateway.backend_status("write")
    profile = snapshot.get("profile", gateway.active_profile)
    backends = snapshot.get("backends") or {}
    order = snapshot.get("order") or []

    if plain:
        print(f"profile={profile}")
        for name in ("cloud", "local"):
            info = backends.get(name) or {}
            print(f"{name}_kind={info.get('kind', '')}")
            print(f"{name}_model={info.get('model', '')}")
            print(f"{name}_base_url={info.get('base_url', '')}")
            print(f"{name}_auth={info.get('auth_mode', 'none')}")
            print(f"{name}_short_circuit={_short_circuit_text(info.get('short_circuit'))}")
        print(f"order={','.join(order)}")
        return

    click.echo(f"Profile: {profile}")
    click.echo("")
    click.echo("Backends:")
    for name in ("cloud", "local"):
        info = backends.get(name) or {}
        model_name = info.get("model") or "—"
        endpoint = info.get("base_url") or "—"
        auth_mode = str(info.get("auth_mode") or "none")
        if auth_mode == "keychain":
            auth_text = "keychain ✅"
        elif auth_mode == "env":
            auth_text = "env ✅"
        elif auth_mode == "missing":
            auth_text = "missing ❌"
        else:
            auth_text = "none"
        last_call = info.get("last_call")
        if isinstance(last_call, dict):
            when = _relative_time_text(str(last_call.get("at") or ""))
            duration_ms = int(last_call.get("duration_ms") or 0)
            duration_text = f"{duration_ms / 1000:.1f}s"
            status_text = "OK" if bool(last_call.get("ok")) else "FAIL"
            last_call_text = f"{when}, {duration_text}, {status_text}"
        else:
            last_call_text = "never"
        short_circuit = _short_circuit_text(info.get("short_circuit"))

        click.echo(f"  [{name}] {model_name}")
        click.echo(f"    Endpoint: {endpoint}")
        click.echo(f"    Auth: {auth_text}")
        click.echo(f"    Last call: {last_call_text}")
        click.echo(f"    Short-circuit: {short_circuit}")
        click.echo("")

    if order == ["cloud", "local"]:
        click.echo("Fallback: cloud → local on 401/403/timeout")
    elif order == ["local", "cloud"]:
        click.echo("Fallback: local → cloud on timeout/connection failure")
    elif order == ["cloud"]:
        click.echo("Fallback: cloud-only")
    elif order == ["local"]:
        click.echo("Fallback: local-only")
    else:
        click.echo("Fallback: disabled")


@model.command("setup")
def model_setup():
    """Interactive setup for cloud/local model backends with keychain storage."""
    cfg = get_config()
    config_path = get_config_path()

    cloud_presets = {
        "1": {
            "name": "豆包",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "model": "doubao-seed-1-6-250615",
            "api_key_env": "ARK_API_KEY",
        },
        "2": {
            "name": "DeepSeek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
        "3": {
            "name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4.1-mini",
            "api_key_env": "OPENAI_API_KEY",
        },
    }
    local_presets = {
        "1": {
            "name": "LM Studio",
            "kind": "lm_studio",
            "base_url": "http://127.0.0.1:1234",
            "model": "qwen3-8b-mlx",
        },
        "2": {
            "name": "Ollama",
            "kind": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "model": "qwen3:8b",
        },
    }
    profile_choices = {
        "1": "cloud-first",
        "2": "local-first",
        "3": "cloud-only",
        "4": "local-only",
    }

    click.echo("")
    click.echo("[1/2] 云端 backend")
    click.echo("  预设：")
    click.echo("    1) 豆包 (https://ark.cn-beijing.volces.com/api/v3)")
    click.echo("    2) DeepSeek (https://api.deepseek.com)")
    click.echo("    3) OpenAI (https://api.openai.com/v1)")
    click.echo("    4) 自定义")
    cloud_choice = str(click.prompt("  选择 [1]", default="1")).strip() or "1"

    if cloud_choice == "4":
        cloud_base_url = str(click.prompt("  Base URL", default="https://api.openai.com/v1")).strip()
        cloud_model_default = "custom-model"
        cloud_api_key_env = "OPENAI_API_KEY"
    else:
        preset = cloud_presets.get(cloud_choice, cloud_presets["1"])
        cloud_base_url = preset["base_url"]
        cloud_model_default = preset["model"]
        cloud_api_key_env = preset["api_key_env"]

    cloud_model = str(click.prompt(f"  Model ID [{cloud_model_default}]", default=cloud_model_default)).strip()
    cloud_api_key = str(click.prompt("  API Key (隐藏输入)", hide_input=True)).strip()

    click.echo("  → 测试连通...")
    cloud_ok, cloud_message, cloud_elapsed = _probe_backend(
        kind="openai_compatible",
        base_url=cloud_base_url,
        model=cloud_model,
        api_key=cloud_api_key,
        timeout_sec=300,
    )
    if cloud_ok:
        click.echo(f"  → 测试连通... ✅ OK ({cloud_elapsed:.1f}s)")
    else:
        click.echo(f"  → 测试连通... ⚠️ {cloud_message}")
        click.echo("  → 仍保存配置（之后修复连通后可直接生效）")

    service_name = "com.keypulse.model.cloud"
    keychain_stored = False
    try:
        store_secret(service_name, cloud_api_key)
        keychain_stored = True
        click.echo(f"  → API Key 已存入 Keychain (service: {service_name})")
    except (KeychainUnavailable, KeychainCommandError) as exc:
        click.echo(f"  → Keychain 写入失败：{exc}")
        click.echo("  → 仍保存配置，你可先用 api_key_env 兜底。")

    click.echo("")
    click.echo("[2/2] 本地 backend (可跳过)")
    click.echo("  预设：")
    click.echo("    1) LM Studio (http://127.0.0.1:1234)")
    click.echo("    2) Ollama (http://127.0.0.1:11434)")
    click.echo("    3) 跳过")
    local_choice = str(click.prompt("  选择 [1]", default="1")).strip() or "1"

    if local_choice == "3":
        local_payload = {
            "kind": "disabled",
            "base_url": "",
            "model": "",
            "timeout_sec": 20,
        }
    else:
        local_preset = local_presets.get(local_choice, local_presets["1"])
        local_model = str(click.prompt(f"  Model ID [{local_preset['model']}]", default=local_preset["model"])).strip()
        click.echo("  → 测试连通...")
        local_ok, local_message, local_elapsed = _probe_backend(
            kind=local_preset["kind"],
            base_url=local_preset["base_url"],
            model=local_model,
            timeout_sec=20,
        )
        if local_ok:
            click.echo(f"  → 测试连通... ✅ OK ({local_elapsed:.1f}s)")
        else:
            click.echo(f"  → 测试连通... ⚠️ {local_message}")
            click.echo("  → 仍保存配置（之后装 chat 模型即可生效）")
        local_payload = {
            "kind": local_preset["kind"],
            "base_url": local_preset["base_url"],
            "model": local_model,
            "timeout_sec": 20,
        }

    click.echo("")
    click.echo("策略：")
    click.echo("  1) cloud-first（推荐）：云端为主，401/超时 fallback 本地")
    click.echo("  2) local-first（隐私优先）")
    click.echo("  3) cloud-only")
    click.echo("  4) local-only")
    policy_choice = str(click.prompt("  选择 [1]", default="1")).strip() or "1"
    active_profile = profile_choices.get(policy_choice, "cloud-first")

    model_payload: dict[str, object] = {
        "active_profile": active_profile,
        "state_path": cfg.model.state_path,
        "local": local_payload,
        "cloud": {
            "kind": "openai_compatible",
            "base_url": cloud_base_url,
            "model": cloud_model,
            "api_key_source": f"keychain:{service_name}",
            "api_key_env": cloud_api_key_env,
            "timeout_sec": 300,
        },
    }

    _write_model_config_atomic(config_path, model_payload)

    click.echo("")
    click.echo(f"✅ 配置已写入 {config_path}")
    if keychain_stored:
        click.echo("   API Key 在 Keychain（不在 config 文件、不需要 shell export）")

    if check_daemon_keychain_access():
        advice = render_plist_advice()
        if advice:
            click.echo("")
            click.echo(f"ℹ️  {advice}")
    else:
        click.echo("")
        click.echo("ℹ️  未能确认 daemon 的 Keychain 访问能力，请稍后用 keypulse model status 检查。")


@model.command("use")
@click.argument("profile")
def model_use(profile):
    """Persist the active model profile."""
    cfg = get_config()
    gateway = load_model_gateway(cfg)
    try:
        gateway.use_profile(profile)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    console.print(f"[green]Model profile set to {profile}[/green]")


@model.command("test")
def model_test():
    """Test the selected model backend."""
    cfg = get_config()
    gateway = load_model_gateway(cfg)
    result = gateway.test_backend()
    if result.get("ok"):
        console.print(f"[green]Model backend ok: {result.get('backend')} / {result.get('model', '—')}[/green]")
    else:
        err_console.print(f"[red]Model backend unavailable: {result.get('message') or result.get('error') or 'unknown'}[/red]")
        sys.exit(1)


# 17. RULES (subgroup)
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


# ═════════════════════════════════════════════════════════════════════════════
# MAINTENANCE
# ═════════════════════════════════════════════════════════════════════════════

@main.group()
def maintenance():
    """Maintenance and cleanup commands."""
    pass


@maintenance.command(name="scrub-secrets")
@click.option("--dry-run", is_flag=True, default=True, help="Preview changes without applying (default: true)")
@click.option("--apply", is_flag=True, default=False, help="Apply redaction (must be explicit)")
def maintenance_scrub_secrets(dry_run, apply):
    """Redact secrets from database and vault."""
    from pathlib import Path
    from keypulse.privacy.desensitizer import desensitize
    from keypulse.utils.paths import get_data_dir

    cfg = get_config()
    require_db(cfg)

    if apply and dry_run:
        err_console.print("[red]Cannot use both --dry-run and --apply together.[/red]")
        sys.exit(1)

    if not apply and not dry_run:
        err_console.print("[yellow]Defaulting to dry-run. Use --dry-run explicitly or add --apply to redact.[/yellow]")
        dry_run = True

    conn = get_conn()

    # Scan raw_events for secrets
    console.print("[cyan]Scanning raw_events for secrets...[/cyan]")
    rows = conn.execute(
        "SELECT id, content_text FROM raw_events WHERE content_text IS NOT NULL ORDER BY id"
    ).fetchall()

    affected_events = []
    for row in rows:
        event_id = row["id"]
        text = row["content_text"]
        redacted = desensitize(text)
        if redacted != text:
            affected_events.append((event_id, text, redacted))

    if affected_events:
        console.print(f"[yellow]Found {len(affected_events)} events with secrets[/yellow]")
        if dry_run:
            console.print("[cyan]Preview (dry-run):[/cyan]")
            for event_id, original, redacted in affected_events[:5]:
                console.print(f"  ID {event_id}:")
                console.print(f"    Before: {original[:80]}")
                console.print(f"    After:  {redacted[:80]}")
            if len(affected_events) > 5:
                console.print(f"  ... and {len(affected_events) - 5} more")
        else:
            for event_id, _, redacted in affected_events:
                conn.execute("UPDATE raw_events SET content_text=? WHERE id=?", (redacted, event_id))
            conn.commit()
            console.print(f"[green]Redacted {len(affected_events)} events in raw_events[/green]")
    else:
        console.print("[green]No secrets found in raw_events[/green]")

    # Scan Obsidian vault for secrets
    vault_dir = Path(cfg.obsidian_vault_path_expanded) if hasattr(cfg, 'obsidian_vault_path_expanded') else None
    if vault_dir and vault_dir.exists():
        console.print("[cyan]Scanning Obsidian vault for secrets...[/cyan]")
        md_files = list(vault_dir.rglob("*.md"))
        affected_files = []

        for md_file in md_files:
            try:
                content = md_file.read_text(encoding="utf-8")
                redacted = desensitize(content)
                if redacted != content:
                    affected_files.append((md_file, content, redacted))
            except Exception as e:
                err_console.print(f"[yellow]Could not read {md_file}: {e}[/yellow]")

        if affected_files:
            console.print(f"[yellow]Found {len(affected_files)} vault files with secrets[/yellow]")
            if dry_run:
                console.print("[cyan]Preview (dry-run):[/cyan]")
                for md_file, _, redacted in affected_files[:3]:
                    console.print(f"  {md_file.relative_to(vault_dir)}")
            else:
                for md_file, _, redacted in affected_files:
                    try:
                        md_file.write_text(redacted, encoding="utf-8")
                    except Exception as e:
                        err_console.print(f"[red]Failed to write {md_file}: {e}[/red]")
                console.print(f"[green]Redacted {len(affected_files)} vault files[/green]")
        else:
            console.print("[green]No secrets found in Obsidian vault[/green]")
    else:
        console.print("[yellow]Obsidian vault not configured or not found[/yellow]")

    if dry_run:
        console.print("\n[cyan]This was a dry-run. To apply redaction, run:[/cyan]")
        console.print("[bold]  keypulse maintenance scrub-secrets --apply[/bold]")


from keypulse.sources.discover import sources_group

main.add_command(sources_group)


if __name__ == "__main__":
    main()
