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
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from penpal.config import Config
from penpal.store.db import init_db, get_conn
from penpal.store.repository import (
    get_sessions,
    get_session_by_id,
    query_raw_events,
    get_state,
    set_state,
    get_all_policies,
    insert_policy,
)
from penpal.store.models import Policy, RawEvent, SearchDoc
from penpal.privacy.desensitizer import desensitize
from penpal.utils.paths import (
    DataDirMigrationError,
    get_data_dir,
    get_db_path,
    get_pid_path,
    get_log_path,
    get_config_path,
)
from penpal.utils.env import get_env_compat
from penpal.utils.paths import get_hud_pid_path
from penpal.utils.atomic_io import atomic_write_text
from penpal.utils.lock import SingleInstanceLock
from penpal.utils.logging import setup_logging
from penpal.app import start_daemon, daemonize, run
from penpal.services.timeline import get_timeline_rows
from penpal.services.stats import get_stats
from penpal.services.export import export_json, export_csv, export_markdown
from penpal.services.sessionizer import sessions_for_today, recent_sessions
from penpal.search.engine import search, recent_clipboard, recent_manual, recent_sessions_docs
from penpal.capture.normalizer import normalize_manual_event
from penpal.capture.capture_control import disabled_sources, is_capture_paused, set_capture_paused
from penpal.capture.keyboard_notice import (
    DISABLE_COMMAND,
    ENABLE_COMMAND,
    KEYBOARD_SOURCE,
    keyboard_capture_enabled,
    mark_notice_shown,
    notice_pending,
    notice_text,
    switch_hint,
)
from penpal.utils.dates import local_day_bounds, resolve_local_date, local_timezone
from penpal.hud import run_hud
from penpal.i18n import CLI_LANG as _CLI_LANG, T, current_lang
from penpal.search.backends import resolve_search_backend


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
    return Path.home() / "Library" / "LaunchAgents" / "com.penpal.daemon.plist"


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _install_logs_dir() -> Path:
    return get_data_dir() / "logs"


def _keypulse_executable_path() -> Path:
    override = (get_env_compat("PENPAL_EXECUTABLE", "") or "").strip()
    candidates = [override, shutil.which("penpal") or ""]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            resolved = shutil.which(str(path))
            path = Path(resolved) if resolved else path
        if path.exists():
            # Keep Homebrew's stable symlink (/opt/homebrew/bin/penpal): resolving it would pin the versioned
            # Cellar path, which `brew upgrade` removes, leaving launchd jobs pointing at a deleted binary.
            return path.absolute()
    raise click.ClickException(
        "Could not find the installed `penpal` executable. "
        "Install with Homebrew first, or set PENPAL_EXECUTABLE=/absolute/path/to/penpal."
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
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.penpal.daemon"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except Exception:
            continue
        if result.returncode == 0:
            return True
    return False


_DEFAULT_USER_CONFIG = """# Penpal user config.
# Homebrew installs the CLI. This file controls the local runtime.

[app]
db_path = "~/.penpal/penpal.db"
log_path = "~/.penpal/penpal.log"
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
    "com.penpal.daemon",
    "com.penpal.healthcheck",
)

# Removed in V1 (legacy report chain, see tag legacy-v0). Cleaned up on install/uninstall.
_LEGACY_LAUNCHD_LABELS = (
    "com.keypulse.daemon",
    "com.keypulse.healthcheck",
    "com.keypulse.hud",
    "com.keypulse.obsidian-sync",
    "com.keypulse.obsidian-sync-hourly",
)


def _launchd_bootout_label(label: str) -> bool:
    commands = [
        ["launchctl", "bootout", f"gui/{os.getuid()}/{label}"],
        ["launchctl", "remove", label],
    ]
    for command in commands:
        try:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
        except Exception:
            continue
        if result.returncode == 0:
            return True
    return False


def _remove_legacy_launchd_jobs() -> list[str]:
    removed: list[str] = []
    for label in _LEGACY_LAUNCHD_LABELS:
        path = _launchd_plist_path(label)
        try:
            loaded = _launchd_label_loaded(label)
            booted_out = _launchd_bootout(path) if path.exists() else _launchd_bootout_label(label) if loaded else True
            if loaded and not booted_out and _launchd_label_loaded(label):
                raise click.ClickException(
                    f"Could not unload legacy launchd service {label}; stop it manually before installing Penpal."
                )
            if _unlink_if_exists(path):
                removed.append(str(path))
        except click.ClickException:
            raise
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
        "com.penpal.daemon": {
            "Label": "com.penpal.daemon",
            "ProgramArguments": [str(executable), "serve"],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 10,
            "StandardOutPath": str(logs / "daemon.log"),
            "StandardErrorPath": str(logs / "daemon.err"),
            "EnvironmentVariables": env,
        },
        "com.penpal.healthcheck": {
            "Label": "com.penpal.healthcheck",
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


def _is_own_versioned_plist(existing: Any, label: str) -> bool:
    """Our own plist from an earlier install that pinned a versioned Homebrew Cellar path (or a binary that is
    gone). Replacing it needs no --force: it is not a user customization, and it breaks on `brew upgrade`."""
    if not isinstance(existing, dict) or existing.get("Label") != label:
        return False
    args = existing.get("ProgramArguments") or []
    program = str(args[0]) if args else ""
    return "/Cellar/penpal/" in program or (program != "" and not Path(program).exists())


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
        if not force and not _is_own_versioned_plist(existing, label):
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
        "Penpal — 本地优先的个人活动记忆基础设施（采集 / 数据源同步 / 搜索 / HUD / 健康）。\n\n"
        "\b\n"
        "常用命令：\n"
        "  penpal status                查看 daemon 状态\n"
        "  penpal doctor                诊断安装 / 配置 / 权限问题\n"
        "  penpal start                 启动后台采集\n"
        "  penpal stop                  停止后台采集\n"
        "  penpal health                运行健康检查（同 healthcheck）\n"
        "  penpal search \"<关键词>\"     搜索历史活动\n\n"
        "完整命令列表见下方 Commands。\n\n"
        "> 语言：中文（设置 PENPAL_LANG=en 切英文）",
        "Penpal — local-first personal activity memory infrastructure (capture / source sync / search / HUD / health).\n\n"
        "\b\n"
        "Common commands:\n"
        "  penpal status                Show daemon status and runtime counters\n"
        "  penpal doctor                Diagnose install, config, and permission issues\n"
        "  penpal start                 Start background capture daemon\n"
        "  penpal stop                  Stop background capture daemon\n"
        "  penpal health                Run health diagnostics and emit JSON (alias: healthcheck)\n"
        "  penpal search \"<keyword>\"    Search historical activity records\n\n"
        "See Commands below for the full command list.\n\n"
        "> Language: English (set PENPAL_LANG=zh to switch to Chinese)"
    )
)
def main():
    try:
        get_data_dir()
    except DataDirMigrationError as exc:
        raise click.ClickException(str(exc)) from exc


def legacy_main():
    """Compatibility entry point for the old command name."""
    click.echo("Deprecated command: use `penpal` instead of `keypulse`.", err=True)
    main()




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


def _format_import_progress(record: dict) -> str:
    """One line for `penpal status` from the last import record (issue #32)."""
    state = record.get("state")
    batches = int(record.get("batches") or 0)
    events = int(record.get("events") or 0)
    resumed = "；上次中断后从游标续传" if record.get("resumed") else ""
    if state == "done":
        return f"已完成：{batches} 批，{events} 条{resumed}"
    if state == "interrupted":
        return f"已中断：已入库 {batches} 批（本次 {events} 条）；重新运行将从游标续传{resumed}"
    if state == "failed":
        return f"写入失败：已入库 {batches} 批；重新运行将从游标续传{resumed}"
    if state == "running":
        return f"进行中：已入库 {batches} 批（若长时间不更新，说明已中断，重新运行即可续传）{resumed}"
    return str(state or "未知")


def _show_keyboard_notice_once() -> None:
    """Print the keyboard capture risk notice the first time install/onboarding runs (issue #29)."""
    if not notice_pending():
        return
    click.echo(notice_text())
    click.echo("")
    mark_notice_shown()


def _stale_moved_dev_venv_script() -> tuple[Path, str] | None:
    bin_dir = get_data_dir() / "venv" / "bin"
    old_venv = Path.home() / ".keypulse" / "venv"
    for name in ("penpal", "keypulse"):
        script = bin_dir / name
        try:
            with script.open(encoding="utf-8") as handle:
                first_line = handle.readline().strip()
        except OSError:
            continue
        if first_line.startswith("#!") and str(old_venv) in first_line:
            return script, first_line[2:].strip()
    return None


@install_group.command("launchd", help="Write and load Penpal launchd jobs.")
@click.option("--no-load", is_flag=True, default=False, help="Write plists without loading launchd")
@click.option("--force", is_flag=True, default=False, help="Replace existing Penpal plists when their content differs")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def install_launchd(no_load, force, plain):
    """Install launchd jobs."""
    if sys.platform != "darwin":
        raise click.ClickException("launchd installation is only supported on macOS.")
    results = _install_launchd_jobs(load=not no_load, force=force)
    _echo_launchd_results(results, plain=plain)


@install_group.command("init", help="Initialize the Penpal runtime after Homebrew install.")
@click.option("--no-launchd", is_flag=True, default=False, help="Skip launchd job installation")
@click.option("--no-load", is_flag=True, default=False, help="Write launchd plists without loading them")
@click.option("--force-config", is_flag=True, default=False, help="Overwrite existing config.toml")
@click.option("--force-launchd", is_flag=True, default=False, help="Replace existing launchd plists when their content differs")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def install_init(no_launchd, no_load, force_config, force_launchd, plain):
    """Initialize runtime."""
    if sys.platform != "darwin":
        raise click.ClickException("Penpal install init is currently macOS-only.")

    data_dir = get_data_dir()
    logs_dir = _install_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    config_status = _write_default_user_config(force=force_config)
    cfg = Config.load()
    init_db(cfg.db_path_expanded)

    launchd_results: list[tuple[str, Path, str, bool]] = []
    if not no_launchd:
        launchd_results = _install_launchd_jobs(load=not no_load, force=force_launchd)

    _show_keyboard_notice_once()
    from penpal.sources.history_gate import historical_imports_allowed

    history_waiting = not historical_imports_allowed()
    if plain:
        print(f"data_dir={data_dir}")
        print(f"logs_dir={logs_dir}")
        print(f"config={config_status}:{get_config_path()}")
        print(f"db_path={cfg.db_path_expanded}")
        for label, path, status_text, loaded in launchd_results:
            print(f"launchd.{label}={status_text},path={path},loaded={loaded}")
        if history_waiting:
            print("next=penpal install onboard")
        return

    console.print("[green]Penpal runtime initialized.[/green]")
    console.print(f"Data: {data_dir}")
    console.print(f"Logs: {logs_dir}")
    console.print(f"Config: {get_config_path()} ({config_status})")
    console.print(f"Database: {cfg.db_path_expanded}")
    if launchd_results:
        _echo_launchd_results(launchd_results)
    console.print("")
    console.print("Next steps:")
    if history_waiting:
        console.print("  penpal install onboard   # 选择要导入的历史来源（之前不读取历史）")
    console.print("  penpal doctor")


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
    for label in _LEGACY_LAUNCHD_LABELS:
        path = _launchd_plist_path(label)
        loaded = _launchd_label_loaded(label)
        checks[f"legacy_launchd.{label}"] = (not path.exists() and not loaded, f"path={path},loaded={loaded}")
    stale_venv = _stale_moved_dev_venv_script()
    if stale_venv:
        checks["dev_venv"] = (False, f"{stale_venv[0]} points to {stale_venv[1]}; rerun install.sh --dev")

    if plain:
        for name, (ok, detail) in checks.items():
            print(f"{name}={'OK' if ok else 'FAIL'} {detail}")
    else:
        table = Table(title="Penpal install doctor", show_header=True, header_style="bold cyan")
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
        raise click.ClickException(f"Refusing to remove path outside ~/.penpal: {target}")
    if target.is_dir():
        shutil.rmtree(target)
        return True
    return _unlink_if_exists(target)


@install_group.command(
    "onboard",
    help=T(
        "首次使用引导：数据边界、逐个确认来源、来源所需权限、首次导入，再引导授予 macOS 权限（辅助功能 / 输入监控）。",
        "First-use guide: data boundary, one-by-one source choice, source permissions, first import, then macOS permission grants.",
    ),
)
@click.option("--non-interactive", is_flag=True, default=False, help="Skip interactive prompts (for CI / automation)")
@click.option(
    "--sources",
    "sources_opt",
    default=None,
    help="Non-interactive choice: comma-separated source names (e.g. claude_code), or 'all' for every source found",
)
@click.option("--yes", "yes_all", is_flag=True, default=False, help="Enable every source found, no prompts (implies --non-interactive)")
def install_onboard(non_interactive: bool, sources_opt: str | None, yes_all: bool):
    """First-use guide: data boundary, source choice, source permissions, first import, then TCC grants."""
    if sys.platform != "darwin":
        raise click.ClickException("install onboard is macOS-only.")
    if yes_all:
        non_interactive = True

    from penpal.permissions import (
        PERMISSION_SPECS,
        PermissionStatus,
        open_settings_pane,
    )

    console.print("[bold]Penpal 首次使用引导[/bold]\n")
    _show_keyboard_notice_once()
    from penpal.sources import onboarding

    for line in onboarding.BOUNDARY_LINES:
        console.print(f"· {line}", markup=False)
    console.print("")

    chosen = _onboard_choose_sources(
        non_interactive=non_interactive, sources_opt=sources_opt, yes_all=yes_all
    )
    chosen_names = sorted(chosen or [])
    _onboard_source_permissions(chosen_names, non_interactive=non_interactive)

    console.print("[bold]Penpal 权限引导[/bold]\n")
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
        console.print(f"  请在系统设置里勾选 Penpal 对此项的授权。")
        click.confirm("  完成后回车继续 →", default=True, show_default=False)

        # Re-check
        recheck = spec.check()
        if recheck == PermissionStatus.GRANTED:
            console.print(f"  [green]✓ 已确认授权[/green]")
        else:
            console.print(
                f"  [yellow]仍未授权——如系统设置里没有 Penpal 条目，"
                f"点击「➕」选择 /Applications/Penpal.app[/yellow]"
            )

    if chosen_names:
        _onboard_first_import(chosen_names)
    else:
        console.print("来源：未启用任何来源，之后可用 `penpal source enable NAME` 打开。\n")

    console.print("\n[bold]权限引导完成，正在重启 daemon 让 TCC 缓存刷新...[/bold]")
    try:
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.penpal.daemon"],
            check=False,
            capture_output=True,
        )
        console.print("[green]daemon 已重启。[/green]运行 `penpal healthcheck` 验证各项状态。")
    except Exception as exc:
        console.print(f"[yellow]daemon 重启失败：{exc}[/yellow]")
        console.print("  请手动执行：launchctl kickstart -k gui/$(id -u)/com.penpal.daemon")


def _onboard_choose_sources(*, non_interactive: bool, sources_opt: str | None, yes_all: bool) -> list[str] | None:
    """Ask about each source found, one by one (or take the scripted choice). Returns the chosen names, or None
    when a non-interactive run gave no choice, in which case the switches are left as they are."""
    from penpal.sources import onboarding
    from penpal.store.db import init_db

    init_db(Config.load().db_path_expanded)
    if sources_opt is None and not yes_all and non_interactive:
        console.print("来源：未指定（非交互模式请加 --sources claude_code，或 --sources all）。开关保持不变。\n")
        return None

    offers = onboarding.offered_sources()
    offered_names = [offer.name for offer in offers]
    if sources_opt is not None and not yes_all:
        requested = [part.strip() for part in sources_opt.split(",") if part.strip()]
        if requested == ["all"]:
            chosen = list(offered_names)
        else:
            unknown = sorted(set(requested) - onboarding.choosable_names())
            if unknown:
                raise click.ClickException(f"unknown source: {', '.join(unknown)} (see `penpal source list`)")
            chosen = requested
    elif yes_all:
        chosen = list(offered_names)
    else:
        if offers:
            console.print("来源：逐个确认。默认不启用，只有你回答 y 的来源才会被读取。\n")
        chosen = []
        for offer in offers:
            label = f"  启用 {offer.name}（{offer.description}；{onboarding.describe_instances(offer)}）？"
            if click.confirm(label, default=False):
                chosen.append(offer.name)
        console.print("")

    applied = onboarding.apply_choice(chosen)
    on = [name for name, enabled in applied.items() if enabled]
    off = [name for name, enabled in applied.items() if not enabled]
    console.print(f"来源已确认：启用 {', '.join(on) or '无'}；关闭 {', '.join(off) or '无'}。\n", markup=False)
    return on


def _onboard_source_permissions(chosen: list[str], *, non_interactive: bool) -> None:
    """Show the macOS permission each chosen source needs, wait for the user to return, then re-check the sources
    with the same availability probe `penpal doctor` uses."""
    from penpal.sources import onboarding

    pairs = onboarding.permissions_for(chosen)
    if not pairs:
        return
    console.print("[bold]已启用来源需要的系统权限[/bold]", markup=True)
    shown: set[str] = set()
    for name, key in pairs:
        console.print(f"  {name} 需要：{onboarding.PERMISSION_LABELS[key]}", markup=False)
        if key not in shown:
            shown.add(key)
            console.print(f"  打开设置：open \"{onboarding.PERMISSION_PANES[key]}\"", markup=False)
    if not non_interactive:
        click.confirm("  在系统设置里完成授权后回车继续 →", default=True, show_default=False)
    _echo_source_recheck(chosen)
    console.print("")


def _echo_source_recheck(chosen: list[str]) -> None:
    from penpal.sources import onboarding
    from penpal.sources.registry import list_sources

    by_name = {plugin.name: plugin for plugin in list_sources()}
    for name in chosen:
        state = onboarding.check_source(name, by_name)
        if state.available:
            console.print(f"  [green]✓[/green] {name}: 可读取", markup=True)
        else:
            console.print(f"  [red]✗[/red] {name}: {state.reason}", markup=False)
            console.print(f"    修复：{onboarding.fix_hint(name, state.reason or '')}", markup=False)


def _onboard_first_import(chosen: list[str]) -> None:
    """First import of the chosen sources, in batches; a failure leaves the saved cursor for the next run."""
    from datetime import datetime, timezone

    from penpal.sources import importer, onboarding

    now = datetime.now(timezone.utc)
    since = onboarding.first_import_since(now)
    console.print("[bold]首次导入[/bold]")
    for name in chosen:
        console.print(f"  {name} …", markup=False)

        def _batch_done(result, _name=name):
            console.print(f"    第 {result.batches} 批已入库（本次 {result.events} 条）", markup=False)

        try:
            result = importer.import_source(name, since=since, until=now, on_batch=_batch_done)
        except Exception as exc:
            console.print(f"    [yellow]导入中断：{exc}。重新运行即可从已保存的游标续传。[/yellow]", markup=False)
            continue
        console.print(f"    {onboarding.format_import_result(result)}", markup=False)
    console.print("")


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
        if path.exists():
            _launchd_bootout(path)
        else:
            _launchd_bootout_label(label)
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

    console.print("[green]Penpal launchd jobs removed.[/green]")
    if removed:
        for item in removed:
            console.print(f"removed: {item}")
    if not remove_data:
        console.print(f"User data preserved under {get_data_dir()}")


# ═════════════════════════════════════════════════════════════════════════════
# 1. START
# ═════════════════════════════════════════════════════════════════════════════

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
    import tomllib
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)


# ═════════════════════════════════════════════════════════════════════════════
# 2. STOP
# ═════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# 3. PAUSE
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("暂停活动采集。", "Pause activity monitoring."))
def pause():
    """Pause monitoring."""
    cfg = get_config()
    require_db(cfg)
    set_capture_paused(True)
    console.print("[yellow]Monitoring paused[/yellow]")


# ═════════════════════════════════════════════════════════════════════════════
# 4. RESUME
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("恢复活动采集。", "Resume activity monitoring."))
def resume():
    """Resume monitoring."""
    cfg = get_config()
    require_db(cfg)
    set_capture_paused(False)
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
    supervised = _launchd_label_loaded("com.penpal.daemon")
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
    # OCR ("屏幕识别补充") is switched off in capture/manager.py, so it is never listed as enabled.

    paused_now = is_capture_paused()
    disabled_now = sorted(disabled_sources())
    from penpal.sources import importer

    import_rows = importer.progress_rows()
    from penpal.sources.history_gate import WAITING_TEXT, historical_imports_allowed

    history_open = historical_imports_allowed()

    if plain:
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
        table.add_row("后台宿主 PID", str(runtime_pid))
        table.add_row("后台宿主路径", str(runtime_host))
        table.add_row("正文采集条数", str(ax_count))
        table.add_row("屏幕识别条数", str(ocr_count))
        console.print(table)


# ═════════════════════════════════════════════════════════════════════════════
# 6. DOCTOR
# ═════════════════════════════════════════════════════════════════════════════

def _is_legacy_mcp_command(command: Any) -> bool:
    if isinstance(command, list):
        command = command[0] if command and isinstance(command[0], str) else ""
    return isinstance(command, str) and Path(command.strip()).name == "keypulse"


def _legacy_mcp_server_details(payload: Any, source: str) -> list[str]:
    details: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {"mcpServers", "mcp_servers"} and isinstance(value, dict):
                for server_name, server_config in value.items():
                    if not isinstance(server_config, dict):
                        continue
                    reasons = []
                    if str(server_name).casefold() == "keypulse":
                        reasons.append("server=keypulse")
                    if _is_legacy_mcp_command(server_config.get("command")):
                        reasons.append("command=keypulse")
                    if reasons:
                        details.append(f"{source}: {', '.join(reasons)}")
            details.extend(_legacy_mcp_server_details(value, source))
    elif isinstance(payload, list):
        for value in payload:
            details.extend(_legacy_mcp_server_details(value, source))
    return details


def _legacy_mcp_config_findings(home: Path | None = None) -> list[str]:
    home = home or Path.home()
    findings: list[str] = []
    claude_config = home / ".claude.json"
    codex_config = home / ".codex" / "config.toml"

    if claude_config.is_file():
        try:
            payload = json.loads(claude_config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = None
        findings.extend(_legacy_mcp_server_details(payload, "~/.claude.json"))

    if codex_config.is_file():
        try:
            payload = tomllib.loads(codex_config.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            payload = None
        findings.extend(_legacy_mcp_server_details(payload, "~/.codex/config.toml"))

    return findings

@main.command(help=T("检查系统配置、权限与依赖。", "Check system configuration, permissions, and dependencies."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def doctor(plain):
    """Run diagnostics."""
    checks = {}
    cfg = get_config()
    require_db(cfg)

    stale_venv = _stale_moved_dev_venv_script()
    if stale_venv:
        checks["开发 venv 入口仍指向旧目录"] = False

    legacy_services = []
    for label in _LEGACY_LAUNCHD_LABELS:
        path = _launchd_plist_path(label)
        loaded = _launchd_label_loaded(label)
        if path.exists() or loaded:
            legacy_services.append((label, path, loaded))
    checks["旧 launchd 服务已清理"] = not legacy_services

    legacy_mcp_configs = _legacy_mcp_config_findings()
    checks["MCP host 配置已迁移为 penpal"] = not legacy_mcp_configs

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

    # macOS permissions needed by the sources that are switched on (read-only; never prompts).
    # Prefers the daemon's own report; falls back to this terminal process when there is none.
    from penpal.health.capture_health import permission_report

    perm_findings = permission_report(cfg)
    for finding in perm_findings:
        if finding.checked_by == "daemon":
            who = f"后台服务，{finding.age_sec} 秒前检查"
        else:
            who = "当前终端进程，非后台服务"
        checks[f"{finding.label} 权限（{', '.join(finding.sources)}；{who}）"] = finding.granted

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
    # OCR is switched off (watcher commented out in capture/manager.py). Shown for information only, never a failure.
    # Information only, never a failure: (table note, plain-output text). Keyboard capture is a user choice.
    keyboard_on = keyboard_capture_enabled(cfg.watchers.keyboard_chunk)
    from penpal.sources.history_gate import WAITING_TEXT, historical_imports_allowed

    info_rows = {
        "屏幕识别（OCR）": ("已停用（OCR 已关闭）", "DISABLED (OCR off)"),
        f"键盘文本片段（{KEYBOARD_SOURCE}）": (
            f"{'开启' if keyboard_on else '关闭'}；{switch_hint(keyboard_on)}",
            (f"ON; to turn off: {DISABLE_COMMAND}" if keyboard_on
             else f"OFF; to turn on again: {ENABLE_COMMAND}"),
        ),
    }
    if not historical_imports_allowed():
        info_rows["历史导入"] = (WAITING_TEXT, "WAITING: run penpal install onboard")
    # Sources that cannot be read right now are a status, not a failure (issue #22). Checked for every source
    # that is switched on, with the fix path (issue #32); a source that was never found is not a fault.
    from penpal.sources.onboarding import source_problems
    from penpal.sources.registry import list_sources

    enabled_now = [plugin.name for plugin in list_sources() if plugin.name not in disabled_sources()]
    unavailable_rows = source_problems(enabled_now)

    if plain:
        for check_name, passed in checks.items():
            status = "OK" if passed else "FAIL"
            print(f"{check_name}: {status}")
        for check_name, (_note, plain_text) in info_rows.items():
            print(f"{check_name}: {plain_text}")
        for row in unavailable_rows:
            print(f"source {row['source']}: UNAVAILABLE reason={row['reason']}; fix: {row['fix']}")
            if row["pane"]:
                print(f"source {row['source']}: open \"{row['pane']}\"")
        if stale_venv:
            print(
                f"venv_entry={stale_venv[0]} points to {stale_venv[1]}; "
                "run `bash install.sh --dev` to rebuild the developer venv"
            )
        for label, path, loaded in legacy_services:
            print(f"legacy_launchd={label},path={path},loaded={loaded}; run `penpal install launchd` to replace it")
        for finding in legacy_mcp_configs:
            print(f"legacy_mcp_config={finding}; change the MCP server name and command to `penpal`")
    else:
        table = Table(title="系统健康检查", show_header=True, header_style="bold cyan")
        table.add_column("检查项")
        table.add_column("结果")
        for check_name, passed in checks.items():
            status = "[green]正常[/green]" if passed else "[red]未通过[/red]"
            table.add_row(check_name, status)
        for check_name, (note, _plain_text) in info_rows.items():
            table.add_row(check_name, note)
        for row in unavailable_rows:
            table.add_row(f"数据源 {row['source']}", f"不可用（{row['reason']}）；{row['fix']}")
        console.print(table)
        if stale_venv:
            console.print(
                f"venv entry {stale_venv[0]} points to {stale_venv[1]}; "
                "run `bash install.sh --dev` to rebuild the developer venv",
                markup=False,
            )
        for label, path, loaded in legacy_services:
            console.print(
                f"Old launchd service remains: {label}, plist={path}, loaded={loaded}; "
                "run `penpal install launchd` to replace it",
                markup=False,
            )
        for finding in legacy_mcp_configs:
            console.print(
                f"Legacy MCP config remains: {finding}; change the MCP server name and command to `penpal`",
                markup=False,
            )
    missing = [f for f in perm_findings if not f.granted]
    for finding in missing:
        if finding.checked_by == "daemon":
            header = f"后台服务缺少权限：{finding.label}（{', '.join(finding.sources)} 需要）。请在系统设置里授予下面的程序："
        else:
            header = (
                f"后台服务状态不可用（未运行或无近期记录），以下只是当前终端进程的检查结果：{finding.label} 未授予"
                f"（{', '.join(finding.sources)} 需要）。采集正常可忽略；异常时请在系统设置里授予下面的程序："
            )
        lines = [
            header,
            *[f"  - {exe}" for exe in finding.executables],
            f"  打开设置面板：open \"{finding.settings_pane}\"",
        ]
        if plain:
            for line in lines:
                print(line)
        else:
            console.print("\n".join(lines), markup=False)
    # Exit with error if any check failed
    if not all(checks.values()):
        sys.exit(1)


@main.command(help=T("运行健康检查，写入 ~/.penpal/health.json。", "Run healthcheck and write ~/.penpal/health.json."))
@click.option("--config", "config_path", default=None)
def healthcheck(config_path):
    """Run healthcheck."""
    from penpal.health import run_healthcheck

    result = run_healthcheck(config_path=config_path)
    click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    if result["overall"] == "alert" and any(alert["severity"] == "error" for alert in result["alerts"]):
        raise SystemExit(1)


main.add_command(healthcheck, name="health")  # D33: the short, discoverable spelling; `healthcheck` stays (launchd uses it)


@main.command("self-heal", hidden=True, help=T("执行自愈流程（HUD/手动恢复）。", "Run product self-heal sequence for HUD/manual recovery."))
@click.option("--dry-run", is_flag=True, help=T("仅演练步骤，不真正重启 daemon", "Dry run only; do not actually restart the daemon"))
def self_heal_command(dry_run):
    """Self-heal."""
    from penpal.health.self_heal import run_self_heal

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
        result = install_app(_keypulse_executable_path(), version=__version__, apps_dir=apps_dir,
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
    from penpal.store.repository import insert_raw_event

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
    from penpal.store.repository import insert_search_doc
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
        "例如 penpal search \"签证 visa 材料\"。AI 客户端（MCP）则由客户端自己提取关键词，见 search_context 工具说明。",
        "Search historical activity. QUERY is split on whitespace into keywords; there is no language "
        "understanding, so give keywords, not a sentence. Example: penpal search \"visa checklist\". "
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
@click.option("--app", default=None, help=T("清理指定应用的数据（精确匹配，不区分大小写）", "Purge data for one app (exact name, case-insensitive)"))
@click.option("--source", default=None, help=T("清理指定来源的数据（精确匹配）", "Purge data from one source (exact match)"))
@click.option("--dry-run", is_flag=True, default=False, help=T("只统计将删除的数量，不删除", "Only count what would be deleted; delete nothing"))
@click.option("--confirm", is_flag=True, default=False, help=T("无需确认提示直接删除", "Confirm deletion without prompt"))
def purge(today, last_hours, app, source, dry_run, confirm):
    """Purge data (raw events plus their derived rows: FTS, evidence, feedback, search docs, sessions)."""
    cfg = get_config()
    require_db(cfg)

    from penpal.services.purge import purge_selector, run_purge

    selector = purge_selector(today=today, last_hours=last_hours, app=app, source=source)
    if selector is None:
        err_console.print("[red]Specify --today, --last-hours, --app or --source[/red]")
        sys.exit(1)

    counts = run_purge(selector, dry_run=True)
    if counts["raw_events"] == 0:
        console.print("[yellow]No data found to delete.[/yellow]")
        return

    console.print("Would delete:" if dry_run else "Will delete:")
    for name, n in counts.items():
        console.print(f"  {name}: {n}")
    if dry_run:
        console.print("[yellow]Dry run: nothing deleted.[/yellow]")
        return

    if not confirm:
        msg = f"Delete {counts['raw_events']} events"
        if app:
            msg += f" from app {app}"
        if source:
            msg += f" from source {source}"
        msg += " and their derived data?"
        if not click.confirm(msg):
            console.print("[yellow]Cancelled.[/yellow]")
            return

    counts = run_purge(selector)
    console.print("[green]Deleted:[/green]")
    for name, n in counts.items():
        console.print(f"  {name}: {n}")
    if source:
        console.print(T("提示：这只删除 Penpal 里的副本，原始文件不受影响；若不想再导入，请用 `penpal source disable` 停用该来源。",
                        "Note: only Penpal's copy is deleted, the original file is untouched; to stop re-importing it, disable the source."))




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


@main.group("privacy", help=T("隐私开关（聊天 App 白名单等）。", "Privacy switches (chat app opt-in, ...)."))
def privacy_group():
    """Privacy group."""


@privacy_group.group("chat-apps", help=T("聊天 App 默认不采集；可逐个放行（放行后归入 Private 桶）。", "Chat apps are not captured by default; opt individual apps in (they land in the Private bucket)."))
def privacy_chat_apps():
    """Chat app opt-in."""


def _chat_app_key_or_error(name: str) -> str:
    from penpal.capture.chat_apps import normalize_opt_ins

    try:
        return normalize_opt_ins([name])[0]
    except ValueError:
        from penpal.capture.chat_apps import CHAT_APPS

        valid = ", ".join(app.key for app in CHAT_APPS)
        raise click.ClickException(f"Unknown chat app {name!r}. Valid names: {valid}")


def _set_chat_app_opt_in(name: str, allowed: bool) -> list[str]:
    from penpal.config import set_chat_app_opt_in

    _chat_app_key_or_error(name)
    key, current = set_chat_app_opt_in(name, allowed)
    verb = "allowed" if allowed else "denied"
    console.print(f"[green]{key} {verb}.[/green] Config: {get_config_path()}")
    if allowed:
        console.print("Note: an allowed chat app is captured into the Private bucket.")
    console.print("Restart the daemon (`penpal stop && penpal start`) for this to take effect.")
    return current


@privacy_chat_apps.command("list", help=T("列出所有已知聊天 App 及是否放行。", "List known chat apps and whether each is allowed."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def privacy_chat_apps_list(plain):
    """List chat apps."""
    from penpal.capture.chat_apps import CHAT_APPS

    allowed = set(get_config().privacy.chat_apps_opt_in)
    if plain:
        for app in CHAT_APPS:
            print(f"{app.key}\t{'allowed' if app.key in allowed else 'denied'}")
        return
    table = Table(title="Chat apps", show_header=True, header_style="bold cyan")
    table.add_column("App")
    table.add_column("Status")
    for app in CHAT_APPS:
        table.add_row(app.key, "allowed (Private bucket)" if app.key in allowed else "denied (not captured)")
    console.print(table)


@privacy_chat_apps.command("allow", help=T("放行某个聊天 App（数据归入 Private 桶）。", "Allow a chat app (its data lands in the Private bucket)."))
@click.argument("name")
def privacy_chat_apps_allow(name):
    """Allow a chat app."""
    _set_chat_app_opt_in(name, True)


@privacy_chat_apps.command("deny", help=T("取消放行某个聊天 App（恢复默认不采集）。", "Deny a chat app again (back to the default: not captured)."))
@click.argument("name")
def privacy_chat_apps_deny(name):
    """Deny a chat app."""
    _set_chat_app_opt_in(name, False)


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
        from penpal.store.buckets import BUCKET_MODE, normalize_bucket

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
    from penpal.store.buckets import reapply_buckets

    changed = reapply_buckets(get_conn())
    console.print(f"[green]Re-bucketed {changed} event(s).[/green]")


@rules.command("disable", help=T("禁用某条隐私策略。", "Disable a privacy policy."))
@click.argument("rule_id", type=int)
def rules_disable(rule_id):
    """Disable rule."""
    cfg = get_config()
    require_db(cfg)

    from penpal.store.db import get_conn
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
    from penpal.privacy.desensitizer import desensitize
    from penpal.utils.paths import get_data_dir

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
        console.print("[bold]  penpal maintenance scrub-secrets --apply[/bold]")


from penpal.sources.discover import sources_group

main.add_command(sources_group)
main.add_command(sources_group, name="source")  # issue #28 spelling: `penpal source disable NAME`

from penpal.mcp.cli import mcp_group

main.add_command(mcp_group)

from penpal.feedback_cli import feedback_group

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
              help="JSONL output path, outside any git work tree (default ~/.penpal/spike/ax-<app>-<ts>.jsonl)")
def spike_ax_text(app_name, seconds, out_path):
    from penpal.capture.ax_spike import run_ax_text_spike
    from penpal.capture.ax_spike_adapter import PyObjCAXAdapter

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
