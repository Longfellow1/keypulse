from __future__ import annotations
import os
import sys
import time
import signal
import subprocess
from pathlib import Path
import click
from rich.table import Table
from penpal.utils.paths import get_hud_lock_path, get_hud_pid_path
from penpal.utils.lock import SingleInstanceLock
from penpal.hud import run_hud
from penpal.i18n import T
from . import main
from ._shared import PLAIN_HELP, console, err_console, get_config, require_db
from .install import _penpal_executable_path


def _hud_lock() -> SingleInstanceLock:
    return SingleInstanceLock(get_hud_lock_path(), get_hud_pid_path())

def _find_legacy_hud_pid() -> int | None:
    try:
        result = subprocess.run(["pgrep", "-f", "penpal hud"], check=False, capture_output=True, text=True)
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
        if "penpal hud" not in command:
            continue
        if any(marker in command for marker in [" hud status", " hud stop", " hud close", " hud start"]):
            continue
        if command.rstrip().endswith("penpal hud") or command.rstrip().endswith("penpal.cli hud"):
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
    pid = _find_legacy_hud_pid()

    if not lock.is_running() and not pid:
        console.print("[yellow]HUD is not running.[/yellow]")
        return
    if not pid:
        err_console.print("[yellow]HUD is running, but its process could not be identified safely.[/yellow]")
        raise SystemExit(1)

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
    is_running = lock.is_running() or pid is not None

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

@main.group(invoke_without_command=True, help=T("管理 macOS 菜单栏 HUD。", "Manage the macOS status bar HUD."))
@click.pass_context
def hud(ctx):
    """HUD."""
    if ctx.invoked_subcommand is None:
        _start_hud()

@hud.command("start", help=T("启动 HUD。", "Launch HUD."))
def hud_start():
    """Start HUD."""
    _start_hud()

@hud.command("stop", help=T("停止 HUD。", "Stop HUD."))
def hud_stop():
    """Stop HUD."""
    _stop_hud()

@hud.command("close", help=T("关闭 HUD。", "Close HUD."))
def hud_close():
    """Close HUD."""
    _stop_hud()

@hud.command("status", help=T("查看 HUD 进程状态。", "Show HUD process status."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def hud_status(plain):
    """HUD status."""
    _show_hud_status(plain=plain)

@main.group("app", help=T("可选桌面 App（菜单栏 HUD）的安装与卸载。", "Install / remove the optional desktop App (menu bar HUD)."))
def app_group():
    """Optional desktop App."""

def _app_dir_option(func):
    func = click.option("--agents-dir", "agents_dir", type=click.Path(path_type=Path), default=None, hidden=True,
                        help="Override the LaunchAgents directory (tests)")(func)
    return click.option("--dir", "apps_dir", type=click.Path(path_type=Path), default=None,
                        help=T("安装目录（默认 ~/Applications）", "Install directory (default ~/Applications)"))(func)

@app_group.command("install", help=T("安装 Penpal.app（只启动已安装的 `penpal hud`，自身不需要系统权限）。",
                                     "Install Penpal.app (only launches the installed `penpal hud`; needs no system permission itself)."))
@click.option("--login", is_flag=True, default=False, help=T("登录时自动启动（注册 com.penpal.hud 启动项）", "Start at login (registers the com.penpal.hud launch agent)"))
@_app_dir_option
def app_install(login, apps_dir, agents_dir):
    """Install Penpal.app."""
    from penpal import __version__
    from penpal.app_bundle import AppBundleError, install_app

    try:
        result = install_app(_penpal_executable_path(), version=__version__, apps_dir=apps_dir,
                             agents_dir=agents_dir, login=login)
    except AppBundleError as exc:
        raise click.ClickException(str(exc)) from exc
    for line in result.actions:
        console.print(f"[green]{line}[/green]")
    console.print(T("提示：App 只是 HUD 的入口，不需要任何系统权限；采集权限仍由后台 daemon 持有。",
                    "Note: the App is only a HUD launcher and needs no system permission; capture permissions stay with the daemon."))

@app_group.command("uninstall", help=T("卸载 Penpal.app 和登录启动项（不影响 daemon 与数据）。", "Remove Penpal.app and its login agent (the daemon and your data are untouched)."))
@_app_dir_option
def app_uninstall(apps_dir, agents_dir):
    """Uninstall Penpal.app."""
    from penpal.app_bundle import AppBundleError, uninstall_app

    try:
        result = uninstall_app(apps_dir=apps_dir, agents_dir=agents_dir)
    except AppBundleError as exc:
        raise click.ClickException(str(exc)) from exc
    for line in result.actions or ["nothing to remove"]:
        console.print(f"[green]{line}[/green]")
