from __future__ import annotations
import json
import os
import locale as _locale
import plistlib
import re
import shutil
import sys
import time
import signal
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

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
)
from keypulse.store.models import Policy, RawEvent, SearchDoc
from keypulse.privacy.desensitizer import desensitize
from keypulse.utils.paths import get_data_dir, get_db_path, get_pid_path, get_log_path, get_config_path
from keypulse.utils.paths import get_hud_pid_path
from keypulse.utils.atomic_io import atomic_write_text
from keypulse.utils.lock import SingleInstanceLock
from keypulse.utils.logging import setup_logging
from keypulse.app import start_daemon, daemonize, run
from keypulse.services.timeline import get_timeline_rows
from keypulse.services.stats import get_stats
from keypulse.services.export import export_json, export_csv, export_markdown
from keypulse.services.sessionizer import sessions_for_today, recent_sessions
from keypulse.search.engine import search, recent_clipboard, recent_manual, recent_sessions_docs
from keypulse.capture.normalizer import normalize_manual_event
from keypulse.utils.dates import local_day_bounds, resolve_local_date, local_timezone
from keypulse.hud import run_hud
from keypulse.i18n import CLI_LANG as _CLI_LANG, T, current_lang
from keypulse.search.backends import resolve_search_backend


# Shared console objects
console = Console()
err_console = Console(stderr=True)
CLI_LANG = _CLI_LANG


DATE_HELP_TEXT = T(
    "日期：today / yesterday / -N（N 天前）/ YYYY-MM-DD",
    "Date: today / yesterday / -N (N days ago) / YYYY-MM-DD",
)
DATE_HELP_TEXT_SHORT = T(
    "日期，支持 today / yesterday / -N(N天前) / 2026-05-19",
    "Date: today / yesterday / -N (N days ago) / YYYY-MM-DD",
)
WEEK_HELP_TEXT = T(
    "周：this / last / YYYY-Www（如 2026-W19）",
    "Week: this / last / YYYY-Www (for example, 2026-W19)",
)
PLAIN_HELP = T("纯文本输出", "Plain text output")


def get_config() -> Config:
    """Load config from standard locations."""
    return Config.load()




def _parse_date(arg: str) -> str:
    raw = (arg or "").strip()
    if not raw:
        raise click.UsageError("`--date` 不能为空")

    normalized = raw.lower()
    if normalized in {"today", "yesterday"}:
        return resolve_local_date(normalized, yesterday=False)

    relative_match = re.fullmatch(r"-(\d+)", normalized)
    if relative_match:
        days_ago = int(relative_match.group(1))
        today_local = datetime.now(local_timezone()).date()
        return (today_local - timedelta(days=days_ago)).isoformat()

    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise click.UsageError(f"`--date` 无效：{raw}。支持 today / yesterday / -N / YYYY-MM-DD。") from exc




def require_db(cfg: Config):
    """Initialize database if not already done."""
    init_db(cfg.db_path_expanded)


def _launchd_daemon_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.keypulse.daemon.plist"


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _install_logs_dir() -> Path:
    return get_data_dir() / "logs"


def _keypulse_executable_path() -> Path:
    override = os.environ.get("KEYPULSE_EXECUTABLE", "").strip()
    candidates = [override, shutil.which("keypulse") or ""]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            resolved = shutil.which(str(path))
            path = Path(resolved) if resolved else path
        if path.exists():
            return path.resolve()
    raise click.ClickException(
        "Could not find the installed `keypulse` executable. "
        "Install with Homebrew first, or set KEYPULSE_EXECUTABLE=/absolute/path/to/keypulse."
    )


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


_DEFAULT_USER_CONFIG = """# KeyPulse user config.
# Homebrew installs the CLI. This file controls the local runtime.

[app]
db_path = "~/.keypulse/keypulse.db"
log_path = "~/.keypulse/keypulse.log"
flush_interval_sec = 5

[watchers]
window = true
idle = true
clipboard = true
manual = true
keyboard_chunk = true
browser = false
browser_url = true
ax_text = false
ocr = false
"""


_LAUNCHD_LABELS = (
    "com.keypulse.daemon",
    "com.keypulse.healthcheck",
)

# Removed in V1 (legacy report chain, see tag legacy-v0). Cleaned up on install/uninstall.
_LEGACY_LAUNCHD_LABELS = (
    "com.keypulse.obsidian-sync",
    "com.keypulse.obsidian-sync-hourly",
)


def _remove_legacy_launchd_jobs() -> list[str]:
    removed: list[str] = []
    for label in _LEGACY_LAUNCHD_LABELS:
        path = _launchd_plist_path(label)
        if not path.exists():
            continue
        try:
            _launchd_bootout(path)
            if _unlink_if_exists(path):
                removed.append(str(path))
        except Exception:
            continue
    return removed


def _default_path_env(executable: Path) -> str:
    parts = [
        str(executable.parent),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    deduped: list[str] = []
    for item in parts:
        if item not in deduped:
            deduped.append(item)
    return ":".join(deduped)


def _launchd_jobs(executable: Path) -> dict[str, dict[str, Any]]:
    logs = _install_logs_dir()
    env = {"PATH": _default_path_env(executable)}
    return {
        "com.keypulse.daemon": {
            "Label": "com.keypulse.daemon",
            "ProgramArguments": [str(executable), "serve"],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "StandardOutPath": str(logs / "daemon.log"),
            "StandardErrorPath": str(logs / "daemon.err"),
            "EnvironmentVariables": env,
        },
        "com.keypulse.healthcheck": {
            "Label": "com.keypulse.healthcheck",
            "ProgramArguments": [str(executable), "healthcheck"],
            "StartInterval": 600,
            "RunAtLoad": True,
            "StandardOutPath": str(logs / "healthcheck.log"),
            "StandardErrorPath": str(logs / "healthcheck.err"),
            "EnvironmentVariables": env,
        },
    }


def _launchd_plist_path(label: str) -> Path:
    return _launch_agents_dir() / f"{label}.plist"


def _write_launchd_plist(label: str, payload: dict[str, Any], *, force: bool = False) -> str:
    path = _launchd_plist_path(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = plistlib.loads(path.read_bytes())
        except Exception:
            existing = None
        if existing == payload:
            return "unchanged"
        if not force:
            raise click.ClickException(f"{path} already exists and differs. Re-run with --force to replace it.")
        status = "updated"
    else:
        status = "created"

    with path.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)
    return status


def _launchd_load_plist(path: Path) -> bool:
    for command in (
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(path)],
        ["launchctl", "unload", str(path)],
    ):
        try:
            subprocess.run(command, check=False, capture_output=True, text=True)
        except OSError:
            pass
    for command in (
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
        ["launchctl", "load", str(path)],
    ):
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
        except OSError:
            continue
        if result.returncode == 0:
            return True
    return False


def _install_launchd_jobs(*, load: bool, force: bool) -> list[tuple[str, Path, str, bool]]:
    executable = _keypulse_executable_path()
    _install_logs_dir().mkdir(parents=True, exist_ok=True)
    _remove_legacy_launchd_jobs()
    results: list[tuple[str, Path, str, bool]] = []
    for label, payload in _launchd_jobs(executable).items():
        status = _write_launchd_plist(label, payload, force=force)
        path = _launchd_plist_path(label)
        loaded = _launchd_load_plist(path) if load else False
        results.append((label, path, status, loaded))
    return results


def _write_default_user_config(*, force: bool = False) -> str:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    if path.exists() and not force:
        return "exists"
    atomic_write_text(path, _DEFAULT_USER_CONFIG)
    return "updated" if existed else "created"


def _load_capture_runtime_state() -> dict | None:
    raw = get_state("capture_runtime")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except Exception:
        return None
    return value if isinstance(value, dict) else None




@click.group(
    help=T(
        "KeyPulse — 本地优先的个人活动记忆基础设施（采集 / 数据源同步 / 搜索 / HUD / 健康）。\n\n"
        "\b\n"
        "常用命令：\n"
        "  keypulse status                查看 daemon 状态\n"
        "  keypulse doctor                诊断安装 / 配置 / 权限问题\n"
        "  keypulse start                 启动后台采集\n"
        "  keypulse stop                  停止后台采集\n"
        "  keypulse healthcheck           运行健康检查\n"
        "  keypulse search \"<关键词>\"     搜索历史活动\n\n"
        "完整命令列表见下方 Commands。\n\n"
        "> 语言：中文（设置 KEYPULSE_LANG=en 切英文）",
        "KeyPulse — local-first personal activity memory infrastructure (capture / source sync / search / HUD / health).\n\n"
        "\b\n"
        "Common commands:\n"
        "  keypulse status                Show daemon status and runtime counters\n"
        "  keypulse doctor                Diagnose install, config, and permission issues\n"
        "  keypulse start                 Start background capture daemon\n"
        "  keypulse stop                  Stop background capture daemon\n"
        "  keypulse healthcheck           Run health diagnostics and emit JSON\n"
        "  keypulse search \"<keyword>\"    Search historical activity records\n\n"
        "See Commands below for the full command list.\n\n"
        "> Language: English (set KEYPULSE_LANG=zh to switch to Chinese)"
    )
)
def main():
    pass




@main.group("install", help="Install, initialize, launchd, and uninstall management.")
def install_group():
    """Install lifecycle group."""
    pass


def _echo_launchd_results(results: list[tuple[str, Path, str, bool]], *, plain: bool = False) -> None:
    for label, path, status_text, loaded in results:
        if plain:
            print(f"{label}={status_text},path={path},loaded={loaded}")
        else:
            load_text = "loaded" if loaded else "not loaded"
            console.print(f"[green]{label}[/green] {status_text} ({load_text}) -> {path}")


@install_group.command("launchd", help="Write and load KeyPulse launchd jobs.")
@click.option("--no-load", is_flag=True, default=False, help="Write plists without loading launchd")
@click.option("--force", is_flag=True, default=False, help="Replace existing KeyPulse plists when their content differs")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def install_launchd(no_load, force, plain):
    """Install launchd jobs."""
    if sys.platform != "darwin":
        raise click.ClickException("launchd installation is only supported on macOS.")
    results = _install_launchd_jobs(load=not no_load, force=force)
    _echo_launchd_results(results, plain=plain)


@install_group.command("init", help="Initialize the KeyPulse runtime after Homebrew install.")
@click.option("--no-launchd", is_flag=True, default=False, help="Skip launchd job installation")
@click.option("--no-load", is_flag=True, default=False, help="Write launchd plists without loading them")
@click.option("--force-config", is_flag=True, default=False, help="Overwrite existing config.toml")
@click.option("--force-launchd", is_flag=True, default=False, help="Replace existing launchd plists when their content differs")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def install_init(no_launchd, no_load, force_config, force_launchd, plain):
    """Initialize runtime."""
    if sys.platform != "darwin":
        raise click.ClickException("KeyPulse install init is currently macOS-only.")

    data_dir = get_data_dir()
    logs_dir = _install_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    config_status = _write_default_user_config(force=force_config)
    cfg = Config.load()
    init_db(cfg.db_path_expanded)

    launchd_results: list[tuple[str, Path, str, bool]] = []
    if not no_launchd:
        launchd_results = _install_launchd_jobs(load=not no_load, force=force_launchd)

    if plain:
        print(f"data_dir={data_dir}")
        print(f"logs_dir={logs_dir}")
        print(f"config={config_status}:{get_config_path()}")
        print(f"db_path={cfg.db_path_expanded}")
        for label, path, status_text, loaded in launchd_results:
            print(f"launchd.{label}={status_text},path={path},loaded={loaded}")
        return

    console.print("[green]KeyPulse runtime initialized.[/green]")
    console.print(f"Data: {data_dir}")
    console.print(f"Logs: {logs_dir}")
    console.print(f"Config: {get_config_path()} ({config_status})")
    console.print(f"Database: {cfg.db_path_expanded}")
    if launchd_results:
        _echo_launchd_results(launchd_results)
    console.print("")
    console.print("Next steps:")
    console.print("  keypulse doctor")


@install_group.command("doctor", help="Check install-level status.")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def install_doctor(plain):
    """Install diagnostics."""
    checks: dict[str, tuple[bool, str]] = {}
    try:
        executable = _keypulse_executable_path()
        checks["executable"] = (executable.exists(), str(executable))
    except click.ClickException as exc:
        checks["executable"] = (False, str(exc))

    config_path = get_config_path()
    checks["config"] = (config_path.exists(), str(config_path))
    cfg = Config.load()
    checks["database"] = (cfg.db_path_expanded.exists(), str(cfg.db_path_expanded))

    for label in _LAUNCHD_LABELS:
        path = _launchd_plist_path(label)
        loaded = _launchd_label_loaded(label)
        checks[f"launchd.{label}"] = (path.exists() and loaded, f"path={path},loaded={loaded}")

    if plain:
        for name, (ok, detail) in checks.items():
            print(f"{name}={'OK' if ok else 'FAIL'} {detail}")
    else:
        table = Table(title="KeyPulse install doctor", show_header=True, header_style="bold cyan")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail")
        for name, (ok, detail) in checks.items():
            table.add_row(name, "[green]OK[/green]" if ok else "[red]FAIL[/red]", detail)
        console.print(table)
    if not all(ok for ok, _detail in checks.values()):
        raise SystemExit(1)


def _unlink_if_exists(path: Path) -> bool:
    if not path.exists():
        return False
    path.unlink()
    return True


def _remove_keypulse_path(path: Path) -> bool:
    data_dir = get_data_dir().resolve()
    target = path.expanduser()
    try:
        resolved = target.resolve(strict=False)
    except OSError:
        resolved = target
    if data_dir != resolved and data_dir not in resolved.parents:
        raise click.ClickException(f"Refusing to remove path outside ~/.keypulse: {target}")
    if target.is_dir():
        shutil.rmtree(target)
        return True
    return _unlink_if_exists(target)


@install_group.command(
    "onboard",
    help=T(
        "首次安装/重打包后引导授予 macOS 权限（辅助功能 / 输入监控）。",
        "Guide the user through granting macOS permissions after first install or repack.",
    ),
)
@click.option("--non-interactive", is_flag=True, default=False, help="Skip interactive prompts (for CI / automation)")
def install_onboard(non_interactive: bool):
    """Drive the user through TCC permission grants."""
    if sys.platform != "darwin":
        raise click.ClickException("install onboard is macOS-only.")

    from keypulse.permissions import (
        PERMISSION_SPECS,
        PermissionStatus,
        open_settings_pane,
    )

    console.print("[bold]KeyPulse 权限引导[/bold]\n")
    console.print(
        "重新打包后 macOS 会撤销以下权限，需要重新授权。"
        "我会按顺序检查每一项，缺失时打开系统设置对应面板。\n"
    )

    for spec in PERMISSION_SPECS:
        status = spec.check()
        if status == PermissionStatus.GRANTED:
            console.print(f"[green]✓[/green] {spec.label}: 已授权")
            continue
        if status == PermissionStatus.UNAVAILABLE:
            console.print(f"[yellow]?[/yellow] {spec.label}: API 不可用（跳过）")
            continue

        console.print(f"[red]✗[/red] {spec.label}: 未授权")

        # Trigger native prompt (will only show if state is undetermined).
        prompt_result = spec.request()
        if prompt_result == PermissionStatus.GRANTED:
            console.print(f"  [green]→ 已通过原生授权框完成[/green]")
            continue

        # State is denied → native prompt won't show, must open settings.
        console.print(f"  打开系统设置面板：{spec.settings_pane}")
        open_settings_pane(spec)
        if non_interactive:
            console.print("  [yellow]non-interactive 模式：跳过等待，请稍后重跑此命令验证[/yellow]")
            continue
        console.print(f"  请在系统设置里勾选 KeyPulse 对此项的授权。")
        click.confirm("  完成后回车继续 →", default=True, show_default=False)

        # Re-check
        recheck = spec.check()
        if recheck == PermissionStatus.GRANTED:
            console.print(f"  [green]✓ 已确认授权[/green]")
        else:
            console.print(
                f"  [yellow]仍未授权——如系统设置里没有 KeyPulse 条目，"
                f"点击「➕」选择 /Applications/KeyPulse.app[/yellow]"
            )

    console.print("\n[bold]权限引导完成，正在重启 daemon 让 TCC 缓存刷新...[/bold]")
    try:
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.keypulse.daemon"],
            check=False,
            capture_output=True,
        )
        console.print("[green]daemon 已重启。[/green]运行 `keypulse healthcheck` 验证各项状态。")
    except Exception as exc:
        console.print(f"[yellow]daemon 重启失败：{exc}[/yellow]")
        console.print("  请手动执行：launchctl kickstart -k gui/$(id -u)/com.keypulse.daemon")


@install_group.command("uninstall", help="Uninstall launchd jobs and optionally remove runtime/data.")
@click.option("--remove-runtime", is_flag=True, default=False, help="Remove logs, pid files, health state, and other runtime files")
@click.option("--remove-data", is_flag=True, default=False, help="Remove config, database, and state files (requires --force)")
@click.option("--force", is_flag=True, default=False, help="Confirm destructive removal")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def install_uninstall(remove_runtime, remove_data, force, plain):
    """Uninstall runtime wiring."""
    if remove_data and not force:
        raise click.ClickException("--remove-data requires --force.")

    removed: list[str] = []
    for label in (*_LAUNCHD_LABELS, *_LEGACY_LAUNCHD_LABELS):
        path = _launchd_plist_path(label)
        _launchd_bootout(path)
        if _unlink_if_exists(path):
            removed.append(str(path))

    if remove_runtime:
        for path in (
            get_pid_path(),
            get_hud_pid_path(),
            get_log_path(),
            get_data_dir() / "health.json",
            _install_logs_dir(),
        ):
            if _remove_keypulse_path(path):
                removed.append(str(path))

    if remove_data:
        cfg = Config.load()
        for path in (
            cfg.db_path_expanded,
            get_config_path(),
        ):
            if _remove_keypulse_path(path):
                removed.append(str(path))

    if plain:
        for item in removed:
            print(f"removed={item}")
        print("data_preserved=" + str(not remove_data))
        return

    console.print("[green]KeyPulse launchd jobs removed.[/green]")
    if removed:
        for item in removed:
            console.print(f"removed: {item}")
    if not remove_data:
        console.print(f"User data preserved under {get_data_dir()}")


# ═════════════════════════════════════════════════════════════════════════════
# 1. START
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("启动 KeyPulse daemon。", "Start the KeyPulse daemon."))
@click.option("--config", "config_path", default=None, help=T("配置文件路径（config.toml）", "Path to config.toml"))
def start(config_path):
    """Start daemon."""
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


@main.command(help=T("在前台运行 KeyPulse（常用于 launchd/supervisor）。", "Run KeyPulse in the foreground (for launchd/supervisor)."))
@click.option("--config", "config_path", default=None, help=T("配置文件路径（config.toml）", "Path to config.toml"))
def serve(config_path):
    """Run foreground service."""
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

@main.command(help=T("停止 KeyPulse daemon。", "Stop the KeyPulse daemon."))
def stop():
    """Stop daemon."""
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

@main.command(help=T("暂停活动采集。", "Pause activity monitoring."))
def pause():
    """Pause monitoring."""
    cfg = get_config()
    require_db(cfg)
    set_state("status", "paused")
    console.print("[yellow]Monitoring paused[/yellow]")


# ═════════════════════════════════════════════════════════════════════════════
# 4. RESUME
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("恢复活动采集。", "Resume activity monitoring."))
def resume():
    """Resume monitoring."""
    cfg = get_config()
    require_db(cfg)
    set_state("status", "running")
    console.print("[green]Monitoring resumed[/green]")


# ═════════════════════════════════════════════════════════════════════════════
# 5. STATUS
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("查看 daemon 状态与采集运行信息。", "Show daemon status and capture runtime details."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def status(plain):
    """Show status."""
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
    ax_running = bool((runtime_watchers.get("ax_text") or {}).get("running"))
    ocr_running = bool((runtime_watchers.get("ocr") or {}).get("running"))
    ax_count = int(runtime_counts.get("ax_text") or 0)
    ocr_count = int(runtime_counts.get("ocr_text") or 0)

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
        print(f"runtime_pid={runtime_pid}")
        print(f"runtime_host={runtime_host}")
        print(f"runtime_ax_count={ax_count}")
        print(f"runtime_ocr_count={ocr_count}")
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
        table.add_row("键盘分块状态", "已启用" if getattr(cfg.watchers, "keyboard_chunk", False) else "未启用")
        table.add_row("正文采集状态", "已运行" if ax_running else "未见运行")
        table.add_row("屏幕识别状态", "已运行" if ocr_running else "未见运行")
        table.add_row("后台宿主 PID", str(runtime_pid))
        table.add_row("后台宿主路径", str(runtime_host))
        table.add_row("正文采集条数", str(ax_count))
        table.add_row("屏幕识别条数", str(ocr_count))
        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 6. DOCTOR
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("检查系统配置、权限与依赖。", "Check system configuration, permissions, and dependencies."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def doctor(plain):
    """Run diagnostics."""
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
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        checks["数据库目录可写"] = True
    except Exception:
        checks["数据库目录可写"] = False

    # Config path exists
    config_path = get_config_path()
    checks["配置文件存在"] = config_path.exists()
    runtime = _load_capture_runtime_state() or {}
    runtime_watchers = runtime.get("watchers") or {}
    if runtime_watchers:
        checks["后台正文采集线程"] = bool((runtime_watchers.get("ax_text") or {}).get("running"))
        checks["后台键盘分块线程"] = bool((runtime_watchers.get("keyboard_chunk") or {}).get("running"))
        checks["后台屏幕识别线程"] = bool((runtime_watchers.get("ocr") or {}).get("running"))

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
    # Exit with error if any check failed
    if not all(checks.values()):
        sys.exit(1)


@main.command(help=T("运行健康检查，写入 ~/.keypulse/health.json。", "Run healthcheck and write ~/.keypulse/health.json."))
@click.option("--config", "config_path", default=None)
def healthcheck(config_path):
    """Run healthcheck."""
    from keypulse.health import run_healthcheck

    result = run_healthcheck(config_path=config_path)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    if result["overall"] == "alert" and any(alert["severity"] == "error" for alert in result["alerts"]):
        raise SystemExit(1)


@main.command("self-heal", hidden=True, help=T("执行自愈流程（HUD/手动恢复）。", "Run product self-heal sequence for HUD/manual recovery."))
@click.option("--dry-run", is_flag=True, help=T("仅演练步骤，不真正重启 daemon", "Dry run only; do not actually restart the daemon"))
def self_heal_command(dry_run):
    """Self-heal."""
    from keypulse.health.self_heal import run_self_heal

    cfg = get_config()
    require_db(cfg)
    result = run_self_heal(dry_run=bool(dry_run), source="cli")
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("status") == "failed":
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


# ═════════════════════════════════════════════════════════════════════════════
# 7. SAVE
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("保存一条手动笔记。", "Save a manual note."))
@click.option("--text", default=None, help=T("要保存的文本", "Text to save"))
@click.option("--tag", default=None, help=T("笔记标签", "Tag for the note"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def save(text, tag, plain):
    """Save note."""
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

@main.command(help=T("查看某天活动时间线。", "Show activity timeline for a day."))
@click.option("--date", default=None, help=DATE_HELP_TEXT)
@click.option("--today", is_flag=True, default=False, help=T("显示今天的时间线", "Show today's timeline"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def timeline(date, today, plain):
    """Timeline."""
    cfg = get_config()
    require_db(cfg)

    # Determine which date to use
    if date is None and today:
        date_str = None  # Will default to today
    elif date:
        date_str = _parse_date(date)
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

@main.command(help=T("查看最近活动条目。", "Show recent activity items."))
@click.option("--type", "item_type", default=None, type=click.Choice(["clipboard", "manual", "session"]),
              help=T("按类型过滤", "Filter by type"))
@click.option("--limit", default=20, help=T("显示条目数", "Number of items to show"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def recent(item_type, limit, plain):
    """Recent items."""
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
                ts_fmt = dt.astimezone(local_timezone()).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_fmt = ts

            table.add_row(ts_fmt, item_type_val, title, body)

        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 10. STATS
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("查看活动统计。", "Show activity statistics."))
@click.option("--days", default=7, help=T("分析天数", "Number of days to analyze"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def stats(days, plain):
    """Stats."""
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

@main.command(
    name="search",
    help=T(
        "搜索历史活动。QUERY 按空格拆成关键词，不做语义理解：请直接写关键词，不要写整句。"
        "例如 keypulse search \"签证 visa 材料\"。AI 客户端（MCP）则由客户端自己提取关键词，见 search_context 工具说明。",
        "Search historical activity. QUERY is split on whitespace into keywords; there is no language "
        "understanding, so give keywords, not a sentence. Example: keypulse search \"visa checklist\". "
        "AI hosts (MCP) extract the keywords themselves, see the search_context tool description.",
    ),
)
@click.argument("query")
@click.option("--app", default=None, help=T("按应用名过滤", "Filter by app name"))
@click.option("--since", default=None, help=T("时间过滤（7d、24h 或 YYYY-MM-DD）", "Time filter (7d, 24h, or YYYY-MM-DD)"))
@click.option("--source", default=None, help=T("按来源过滤（clipboard、manual、session）", "Filter by source (clipboard, manual, session)"))
@click.option("--limit", default=50, help=T("结果数量", "Number of results"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def search_cmd(query, app, since, source, limit, plain):
    """Search."""
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
                ts_fmt = dt.astimezone(local_timezone()).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                ts_fmt = ts

            ref_type = result.get("ref_type", "—")
            app_name = result.get("app_name", "—")

            table.add_row(ts_fmt, ref_type, app_name, content)

        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 12. SESSION (subgroup)
# ═════════════════════════════════════════════════════════════════════════════

@main.group(help=T("管理会话。", "Manage sessions."))
def session():
    """Session group."""
    pass


@session.command("list", help=T("列出会话。", "List sessions."))
@click.option("--date", default=None, help=DATE_HELP_TEXT)
@click.option("--limit", default=100, help=T("显示会话数", "Number of sessions to show"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def session_list(date, limit, plain):
    """List sessions."""
    cfg = get_config()
    require_db(cfg)

    parsed_date = _parse_date(date) if date else None
    sessions = get_sessions(date_str=parsed_date, limit=limit)

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
                start = start_dt.astimezone(local_timezone()).strftime("%H:%M:%S")
            except Exception:
                pass

            try:
                end_dt = datetime.fromisoformat(end)
                end = end_dt.astimezone(local_timezone()).strftime("%H:%M:%S")
            except Exception:
                pass

            table.add_row(session_id, start, end, app, title, duration)

        console.print(table)


@session.command("show", help=T("查看单个会话详情。", "Show details for one session."))
@click.argument("session_id")
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def session_show(session_id, plain):
    """Show session."""
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
# 13.9 DEV (internal command entrypoint)
# ═════════════════════════════════════════════════════════════════════════════

@main.group(help=T("开发者内部命令。", "Developer internal commands."))
def dev():
    """Dev group."""
    pass


@dev.command("self-heal", help=T("执行自愈流程（开发入口）。", "Run self-heal sequence (dev entry)."))
@click.option("--dry-run", is_flag=True, help=T("仅演练步骤，不真正重启 daemon", "Dry run only; do not actually restart the daemon"))
def dev_self_heal(dry_run):
    """Dev self-heal."""
    return self_heal_command.callback(dry_run)




# ═════════════════════════════════════════════════════════════════════════════
# 14. EXPORT
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("导出活动数据。", "Export activity data."))
@click.option("--format", type=click.Choice(["json", "csv", "md"]), default="json",
              help=T("导出格式", "Export format"))
@click.option("--days", default=None, type=int, help=T("导出天数", "Number of days to export"))
@click.option("--date", default=None, help=DATE_HELP_TEXT)
@click.option("--output", default=None, help=T("输出文件路径", "Output file path"))
def export(format, days, date, output):
    """Export data."""
    cfg = get_config()
    require_db(cfg)
    parsed_date = _parse_date(date) if date else None

    if format == "json":
        data = export_json(days=days, date_str=parsed_date)
    elif format == "csv":
        data = export_csv(days=days, date_str=parsed_date)
    elif format == "md":
        data = export_markdown(days=days, date_str=parsed_date)
    else:
        err_console.print(f"[red]Unknown format: {format}[/red]")
        sys.exit(1)

    if output:
        Path(output).write_text(data, encoding="utf-8")
        console.print(f"[green]Exported to {output}[/green]")
    else:
        print(data)


# ═════════════════════════════════════════════════════════════════════════════
# 15. PURGE
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("删除活动数据。", "Delete activity data."))
@click.option("--today", is_flag=True, default=False, help=T("清理今天的数据", "Purge today's data"))
@click.option("--last-hours", type=int, default=None, help=T("清理最近 N 小时数据", "Purge last N hours"))
@click.option("--app", default=None, help=T("清理指定应用的数据", "Purge data for specific app"))
@click.option("--confirm", is_flag=True, default=False, help=T("无需确认提示直接删除", "Confirm deletion without prompt"))
def purge(today, last_hours, app, confirm):
    """Purge data."""
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

@main.group(name="config", help=T("管理配置。", "Manage configuration."))
def config_group():
    """Config group."""
    pass


@config_group.command("show", help=T("查看当前配置。", "Show current configuration."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def config_show(plain):
    """Show config."""
    cfg = get_config()

    if plain:
        print(f"db_path={cfg.app.db_path}")
        print(f"log_path={cfg.app.log_path}")
        print(f"flush_interval_sec={cfg.app.flush_interval_sec}")
        print("retention=permanent (D32)")
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


@config_group.command("path", help=T("显示配置文件路径。", "Show config file path."))
def config_path():
    """Config path."""
    path = get_config_path()
    print(str(path))


# ═════════════════════════════════════════════════════════════════════════════
# 17. RULES (subgroup)
# ═════════════════════════════════════════════════════════════════════════════

@main.group(help=T("管理隐私策略。", "Manage privacy policies."))
def rules():
    """Rules group."""
    pass


@rules.command("list", help=T("列出全部隐私策略。", "List all privacy policies."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def rules_list(plain):
    """List rules."""
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


@rules.command("add", help=T("新增隐私策略或分桶规则。", "Add a privacy policy or a bucket rule."))
@click.option("--scope-type", required=True, help=T("范围类型（app、window、source、content；分桶规则仅 app / source）", "Scope type (app, window, source, content; bucket rules: app or source only)"))
@click.option("--scope-value", required=True, help=T("范围值（例如 'Safari'、'password'）", "Scope value (e.g., 'Safari', 'password')"))
@click.option("--mode", default=None, help=T("模式（allow、deny、metadata-only、redact、truncate）", "Mode (allow, deny, metadata-only, redact, truncate)"))
@click.option("--bucket", default=None, help=T("分桶规则：把该 App / 来源的数据归入此桶（Work、Personal、Private、Financial）", "Bucket rule: file data from this app/source into a bucket (Work, Personal, Private, Financial)"))
@click.option("--priority", type=int, default=100, help=T("优先级（值越小优先级越高）", "Priority (lower = higher priority)"))
def rules_add(scope_type, scope_value, mode, bucket, priority):
    """Add rule."""
    cfg = get_config()
    require_db(cfg)

    config_json = None
    if bucket:
        from keypulse.store.buckets import BUCKET_MODE, normalize_bucket

        if mode:
            raise click.UsageError("--bucket and --mode are mutually exclusive")
        if scope_type not in ("app", "source"):
            raise click.UsageError("bucket rules support --scope-type app or source")
        try:
            config_json = json.dumps({"bucket": normalize_bucket(bucket)})
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        mode = BUCKET_MODE
    elif not mode:
        raise click.UsageError("one of --mode or --bucket is required")

    policy = Policy(
        scope_type=scope_type,
        scope_value=scope_value,
        mode=mode,
        priority=priority,
        config_json=config_json,
    )

    policy_id = insert_policy(policy)
    console.print(f"[green]Policy added (ID: {policy_id})[/green]")


@rules.command("rebucket", help=T("按当前分桶规则重新计算已有数据的桶（规则变更不会自动回溯）。", "Re-evaluate buckets of stored events with the current rules (rule changes are not retroactive otherwise)."))
def rules_rebucket():
    """Reapply bucket rules to existing rows."""
    cfg = get_config()
    require_db(cfg)
    from keypulse.store.buckets import reapply_buckets

    changed = reapply_buckets(get_conn())
    console.print(f"[green]Re-bucketed {changed} event(s).[/green]")


@rules.command("disable", help=T("禁用某条隐私策略。", "Disable a privacy policy."))
@click.argument("rule_id", type=int)
def rules_disable(rule_id):
    """Disable rule."""
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

@main.group(help=T("维护与清理命令。", "Maintenance and cleanup commands."))
def maintenance():
    """Maintenance group."""
    pass


@maintenance.command(name="scrub-secrets", help=T("扫描并脱敏数据库内的敏感信息。", "Scan and redact secrets from the database."))
@click.option("--dry-run", is_flag=True, default=True, help=T("预览变更但不应用（默认 true）", "Preview changes without applying (default: true)"))
@click.option("--apply", is_flag=True, default=False, help=T("应用脱敏（必须显式指定）", "Apply redaction (must be explicit)"))
def maintenance_scrub_secrets(dry_run, apply):
    """Scrub secrets."""
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

    if dry_run:
        console.print("\n[cyan]This was a dry-run. To apply redaction, run:[/cyan]")
        console.print("[bold]  keypulse maintenance scrub-secrets --apply[/bold]")


from keypulse.sources.discover import sources_group

main.add_command(sources_group)

from keypulse.mcp.cli import mcp_group

main.add_command(mcp_group)

from keypulse.feedback_cli import feedback_group

main.add_command(feedback_group)


@main.group("spike", hidden=True, help="Throwaway spike tools (M2-4 / #64). Not part of the product.")
def spike_group():
    pass


@spike_group.command(
    "ax-text",
    help="Spike: read committed text of the focused field via AXValue diffs. Needs Accessibility permission.",
)
@click.option("--app", "app_name", required=True, help="Target app name, e.g. Obsidian or 'Google Chrome'")
@click.option("--seconds", default=60.0, show_default=True, type=click.FloatRange(min=1.0, max=3600.0))
@click.option("--out", "out_path", default=None,
              help="JSONL output path, outside any git work tree (default ~/.keypulse/spike/ax-<app>-<ts>.jsonl)")
def spike_ax_text(app_name, seconds, out_path):
    from keypulse.capture.ax_spike import run_ax_text_spike
    from keypulse.capture.ax_spike_adapter import PyObjCAXAdapter

    code = run_ax_text_spike(
        app_name=app_name,
        seconds=seconds,
        out=out_path,
        adapter=PyObjCAXAdapter(),
        cfg_loader=Config.load,
        echo=lambda message: click.echo(message, err=True),
    )
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()
