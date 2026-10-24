from __future__ import annotations
import os
import sys
import time
import signal
import click
from rich.table import Table
from penpal.config import Config, read_config
from penpal.store.repository import get_state
from penpal.utils.paths import get_pid_path
from penpal.utils.lock import SingleInstanceLock
from penpal.runtime import RuntimeState, evaluate, state_message, state_text
from penpal.app import daemonize, run
from penpal.capture.capture_control import disabled_sources, set_capture_paused
from penpal.i18n import T
from . import main
from ._shared import PLAIN_HELP, _runtime_fix_command, _runtime_status_line, console, err_console, get_config, require_db
from .install import _format_import_progress, _launchd_bootout, _launchd_bootstrap, _launchd_daemon_plist_path, _launchd_label_loaded

@main.command(help=T("启动 Penpal daemon。", "Start the Penpal daemon."))
@click.option("--config", "config_path", default=None, help=T("配置文件路径（config.toml）", "Path to config.toml"))
def start(config_path):
    """Start daemon."""
    cfg = Config.load() if not config_path else _load_config_from(config_path)
    lock = SingleInstanceLock()
    launchd_plist = _launchd_daemon_plist_path()

    if launchd_plist.exists():
        if _launchd_label_loaded("com.penpal.daemon"):
            err_console.print(f"[yellow]Already running under launchd (PID {lock.get_pid() or 'unknown'}).[/yellow]")
            sys.exit(1)
        if _launchd_bootstrap(launchd_plist):
            time.sleep(0.5)
            console.print(f"[green]Penpal started via launchd (PID {lock.get_pid() or 'unknown'}).[/green]")
            return

    if lock.is_running():
        err_console.print(f"[red]Already running (PID {lock.get_pid()})[/red]")
        sys.exit(1)

    # Init DB in parent so errors surface here
    require_db(cfg)

    # Warn about Accessibility permission before forking (visible to user)
    from penpal.app import _check_accessibility
    if not _check_accessibility():
        console.print(
            "[yellow]⚠  Accessibility permission not granted.[/yellow]\n"
            "   Window titles won't be captured until you allow access:\n"
            "   System Settings → Privacy & Security → Accessibility → Penpal\n"
            "   Run [bold]penpal doctor[/bold] to recheck."
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
            console.print(f"[green]Penpal started (PID {daemon_pid})[/green]")
        else:
            console.print("[green]Penpal started[/green]")

@main.command(help=T("在前台运行 Penpal（常用于 launchd/supervisor）。", "Run Penpal in the foreground (for launchd/supervisor)."))
@click.option("--config", "config_path", default=None, help=T("配置文件路径（config.toml）", "Path to config.toml"))
def serve(config_path):
    """Run foreground service."""
    cfg = Config.load() if not config_path else _load_config_from(config_path)
    run(cfg)

def _load_config_from(path: str) -> Config:
    """Load config from explicit path."""
    return read_config(path)

@main.command(help=T("停止 Penpal daemon。", "Stop the Penpal daemon."))
def stop():
    """Stop daemon."""
    lock = SingleInstanceLock()
    pid = lock.get_pid()
    launchd_plist = _launchd_daemon_plist_path()

    if launchd_plist.exists() and _launchd_label_loaded("com.penpal.daemon"):
        if _launchd_bootout(launchd_plist):
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for _ in range(50):
                time.sleep(0.1)
                if not lock.is_running():
                    console.print("[green]Penpal stopped and launchd supervision disabled.[/green]")
                    return
            console.print("[yellow]launchd 已卸载，但旧进程仍在退出中。[/yellow]")
            return
        console.print("[yellow]Penpal is supervised by launchd, but unload failed.[/yellow]")
        return

    if not pid:
        console.print("[yellow]Penpal is not running.[/yellow]")
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        console.print("[yellow]Penpal is not running.[/yellow]")
        return

    # Wait up to 5 seconds for process to exit
    for _ in range(50):
        time.sleep(0.1)
        if not lock.is_running():
            console.print("[green]Penpal stopped.[/green]")
            return

    console.print("[yellow]Stop signal sent (may still be shutting down).[/yellow]")

@main.command(help=T("暂停活动采集。", "Pause activity monitoring."))
def pause():
    """Pause monitoring."""
    cfg = get_config()
    require_db(cfg)
    set_capture_paused(True)
    console.print("[yellow]Monitoring paused[/yellow]")

@main.command(help=T("恢复活动采集。", "Resume activity monitoring."))
def resume():
    """Resume monitoring."""
    cfg = get_config()
    require_db(cfg)
    set_capture_paused(False)
    console.print("[green]Monitoring resumed[/green]")

@main.command(help=T("查看 daemon 状态与采集运行信息。", "Show daemon status and capture runtime details."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def status(plain):
    """Show status."""
    cfg = get_config()
    require_db(cfg)

    runtime_status = evaluate()
    pid = runtime_status.pid
    is_running = runtime_status.state.value not in {"not_installed", "not_running"}
    supervised = runtime_status.managed
    status_val = (
        "paused" if runtime_status.state == RuntimeState.PAUSED
        else "stopped" if runtime_status.state in {RuntimeState.NOT_INSTALLED, RuntimeState.NOT_RUNNING}
        else "running"
    )
    started_at = get_state("started_at") or "—"
    last_flush = get_state("last_flush") or "—"
    components = runtime_status.components
    ax_running = components.get("ax_text") is not None and components["ax_text"].state == "running"

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
    if getattr(cfg.watchers, "keyboard_chunk", False):
        enabled.append("键盘分块")
    if cfg.watchers.browser:
        enabled.append("浏览器")
    if getattr(cfg.watchers, "ax_text", False):
        enabled.append("当前看到的正文")
    # OCR ("屏幕识别补充") is switched off in capture/manager.py, so it is never listed as enabled.

    paused_now = runtime_status.state.value == "paused"
    disabled_now = sorted(disabled_sources())
    from penpal.sources import importer

    import_rows = importer.progress_rows()
    from penpal.sources.history_gate import WAITING_TEXT, historical_imports_allowed

    history_open = historical_imports_allowed()

    if plain:
        print(f"runtime_state={runtime_status.code}")
        if runtime_status.issues:
            print(f"runtime_message={state_message(runtime_status)}")
        print(f"running={is_running}")
        print(f"pid={pid or 'none'}")
        print(f"supervised_by_launchd={supervised}")
        print(f"status={status_val}")
        print(f"paused={paused_now}")
        print(f"disabled_sources={','.join(disabled_now)}")
        for source_name, record in import_rows:
            print(
                f"import.{source_name}={record.get('state')},batches={record.get('batches', 0)},"
                f"events={record.get('events', 0)},resumed={bool(record.get('resumed'))},"
                f"updated_at={record.get('updated_at', '')}"
            )
        print(f"history_imports={'enabled' if history_open else 'waiting_onboarding'}")
        print(f"started_at={started_at}")
        print(f"db_path={db_path}")
        print(f"db_size_mb={db_size_mb:.2f}")
        print(f"last_flush={last_flush}")
        print(f"enabled_watchers={','.join(enabled)}")
        print(f"runtime_ax_running={ax_running}")
        runtime_fix = _runtime_fix_command(runtime_status)
        if runtime_fix:
            print(f"runtime_fix={runtime_fix}")
    else:
        status_label = {
            "running": "运行中",
            "paused": "已暂停",
            "stopped": "已停止",
            "unknown": "未知",
        }.get(status_val, status_val)
        table = Table(title="采集状态", show_header=False, box=None)
        table.add_row("服务状态", _runtime_status_line(runtime_status))
        if runtime_status.issues:
            table.add_row("运行详情", state_message(runtime_status))
        if pid is not None:
            table.add_row("进程 PID", str(pid))
        table.add_row("运行方式", "launchd 托管" if supervised else "手动启动")
        table.add_row("采集阶段", status_label)
        table.add_row("全局暂停", "[yellow]是[/yellow]" if paused_now else "否")
        table.add_row("已关闭的数据源", "、".join(disabled_now) if disabled_now else "无")
        if not history_open:
            table.add_row("历史导入", WAITING_TEXT)
        for source_name, record in import_rows:
            table.add_row(f"数据导入（{source_name}）", _format_import_progress(record))
        table.add_row("启动时间", started_at)
        table.add_row("数据库路径", str(db_path))
        table.add_row("数据库大小", f"{db_size_mb:.2f} MB")
        table.add_row("最近一次写入", last_flush)
        table.add_row("已启用采集源", "、".join(enabled) if enabled else "无")
        table.add_row("键盘分块状态", "已启用" if getattr(cfg.watchers, "keyboard_chunk", False) else "未启用")
        table.add_row("正文采集状态", "已运行" if ax_running else "未见运行")
        table.add_row("屏幕识别状态", "已停用（OCR 已关闭）")
        runtime_fix = _runtime_fix_command(runtime_status)
        if runtime_fix:
            table.add_row("修复命令", runtime_fix)
        console.print(table)
