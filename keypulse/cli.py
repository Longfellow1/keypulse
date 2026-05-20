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
from typing import Any, Optional
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
from keypulse.utils.dates import local_day_bounds, resolve_local_date, local_timezone
from keypulse.hud import run_hud
from keypulse.pipeline import (
    append_feedback_event,
    read_feedback_events,
    FeedbackEvent,
    load_model_gateway,
    record_theme_feedback,
    current_theme_profile,
)
from keypulse.pipeline.model import LLMCallError, ModelGateway
from keypulse.pipeline.model_keychain import (
    KeychainCommandError,
    KeychainUnavailable,
    check_daemon_keychain_access,
    read_secret,
    render_plist_advice,
    store_secret,
)
from keypulse.pipeline.daily_orchestrator import DailyOrchestratorError, run_daily, run_daily_after_obsidian_sync
from keypulse.pipeline.daily_summary import read_daily_summary, render_daily_markdown
from keypulse.pipeline.onboarding import (
    OnboardingAnswers,
    QUESTIONS,
    is_first_run,
    validate_answers,
    write_profile,
)
from keypulse.pipeline.weekly_orchestrator import WeeklyOrchestratorError, run_weekly
from keypulse.search.backends import resolve_search_backend


# Shared console objects
console = Console()
err_console = Console(stderr=True)


def _detect_lang() -> str:
    """Detect CLI language. Priority: KEYPULSE_LANG env > LANG env > system locale > 'en'."""
    explicit = os.environ.get("KEYPULSE_LANG", "").strip().lower()
    if explicit in ("zh", "en"):
        return explicit
    lang_env = os.environ.get("LANG", "") or os.environ.get("LC_ALL", "")
    if "zh" in lang_env.lower():
        return "zh"
    try:
        sys_locale = (_locale.getlocale()[0] or "").lower()
        if "zh" in sys_locale or "chinese" in sys_locale:
            return "zh"
    except Exception:
        pass
    return "en"


CLI_LANG = _detect_lang()


def T(zh: str, en: str) -> str:
    """Bilingual helper. Chinese on zh locale, English otherwise."""
    return zh if CLI_LANG == "zh" else en


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


def _parse_cost_ts(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_cost_rows(cost_path: Path):
    if not cost_path.exists():
        return
    with cost_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _month_bounds(month_text: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.strptime(month_text, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise click.UsageError("`--month` format must be YYYY-MM") from exc
    year = start.year + 1 if start.month == 12 else start.year
    month = 1 if start.month == 12 else start.month + 1
    end = start.replace(year=year, month=month)
    return start, end


def _week_bounds(week_text: str) -> tuple[datetime, datetime]:
    match = re.fullmatch(r"(\d{4})-W(\d{2})", week_text.strip())
    if not match:
        raise click.UsageError("`--week` format must be YYYY-Www")
    year = int(match.group(1))
    week = int(match.group(2))
    try:
        start_date = datetime.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise click.UsageError("`--week` is not a valid ISO week") from exc
    start = start_date.replace(tzinfo=timezone.utc)
    return start, start + timedelta(days=7)


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


def _parse_week(arg: str) -> str:
    raw = (arg or "").strip()
    if not raw:
        raise click.UsageError("`--week` 不能为空")

    normalized = raw.lower()
    if normalized in {"this", "last"}:
        base_day = datetime.now(local_timezone()).date()
        if normalized == "last":
            base_day = base_day - timedelta(days=7)
        iso = base_day.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"

    match = re.fullmatch(r"(\d{4})-W(\d{1,2})", raw)
    if not match:
        raise click.UsageError("`--week` 支持 this / last / YYYY-Www")
    year = int(match.group(1))
    week = int(match.group(2))
    try:
        datetime.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise click.UsageError("`--week` is not a valid ISO week") from exc
    return f"{year:04d}-W{week:02d}"


def _aggregate_cost(cost_path: Path, start: datetime, end: datetime) -> tuple[float, dict[str, dict[str, float | int]]]:
    total = 0.0
    per_capability: dict[str, dict[str, float | int]] = {}
    for row in _iter_cost_rows(cost_path):
        ts = _parse_cost_ts(str(row.get("ts") or ""))
        if ts is None or not (start <= ts < end):
            continue
        cost = float(row.get("cost_usd") or 0.0)
        capability = str(row.get("capability") or "unknown")
        total += cost
        bucket = per_capability.setdefault(capability, {"cost_usd": 0.0, "calls": 0, "cache_hits": 0})
        bucket["cost_usd"] = float(bucket["cost_usd"]) + cost
        bucket["calls"] = int(bucket["calls"]) + 1
        if bool(row.get("cache_hit")):
            bucket["cache_hits"] = int(bucket["cache_hits"]) + 1
    return total, per_capability


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
retention_days = 30

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

[obsidian]
vault_path = "~/Documents/KeyPulse"
vault_name = "KeyPulse"
export_hour = 9
export_minute = 5
wiki_link_mode = "relative"
humanize_titles = false

[integration]
standalone_output_path = "~/Documents/KeyPulse"
state_path = "~/.keypulse/sink-state.json"

[model]
active_profile = "local-first"
state_path = "~/.keypulse/model-state.json"

[model.local]
kind = "lm_studio"
base_url = "http://127.0.0.1:1234"
model = "keypulse-local"
api_key_env = ""

[model.cloud]
kind = "openai_compatible"
base_url = "https://api.openai.com/v1"
model = "keypulse-cloud"
api_key_env = "OPENAI_API_KEY"
"""


_LAUNCHD_LABELS = (
    "com.keypulse.daemon",
    "com.keypulse.healthcheck",
    "com.keypulse.obsidian-sync",
    "com.keypulse.obsidian-sync-hourly",
)


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
        "com.keypulse.obsidian-sync": {
            "Label": "com.keypulse.obsidian-sync",
            "ProgramArguments": [str(executable), "obsidian", "sync"],
            "StartCalendarInterval": [
                {"Hour": 12, "Minute": 0},
                {"Hour": 13, "Minute": 0},
                {"Hour": 18, "Minute": 0},
                {"Hour": 21, "Minute": 0},
            ],
            "RunAtLoad": False,
            "KeepAlive": False,
            "StandardOutPath": str(logs / "obsidian-sync.log"),
            "StandardErrorPath": str(logs / "obsidian-sync.err"),
            "EnvironmentVariables": env,
        },
        "com.keypulse.obsidian-sync-hourly": {
            "Label": "com.keypulse.obsidian-sync-hourly",
            "ProgramArguments": [str(executable), "obsidian", "sync", "--incremental"],
            "StartInterval": 3600,
            "ThrottleInterval": 60,
            "RunAtLoad": False,
            "KeepAlive": False,
            "StandardOutPath": str(logs / "obsidian-sync-hourly.log"),
            "StandardErrorPath": str(logs / "obsidian-sync-hourly.err"),
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


def _keychain_secret_exists(source: str) -> bool:
    if not source.startswith("keychain:"):
        return False
    service = source.split(":", 1)[1].strip()
    if not service:
        return False
    try:
        return bool(read_secret(service))
    except (KeychainUnavailable, KeychainCommandError):
        return False


def _cloud_api_key_available(api_key_source: str, api_key_env: str) -> bool:
    env_name = api_key_env.strip()
    if env_name and os.environ.get(env_name):
        return True
    return _keychain_secret_exists(api_key_source.strip())


def _model_backends_need_setup(cfg: Config) -> bool:
    local = cfg.model.local
    cloud = cfg.model.cloud

    local_available = local.kind != "disabled" and bool(local.base_url.strip()) and bool(local.model.strip())
    if local_available:
        return False

    cloud_available = cloud.kind != "disabled" and _cloud_api_key_available(
        cloud.api_key_source,
        cloud.api_key_env,
    )

    return not cloud_available


def _warn_if_model_backends_need_setup(cfg: Config) -> None:
    if not _model_backends_need_setup(cfg):
        return
    click.secho("⚠️  KeyPulse 还没配置可用的模型后端", fg="red", err=True)
    click.secho("   跑 `keypulse setup` 完成首次配置（约 2 分钟）", fg="red", err=True)
    click.secho("   daemon 仍会照常启动，但叙事 / 报告功能会失败", fg="red", err=True)


@click.group(
    help=T(
        "KeyPulse — 个人活动监控与日报/周报生成。\n\n"
        "\b\n"
        "常用命令：\n"
        "  keypulse status                查看 daemon 状态\n"
        "  keypulse doctor                诊断安装 / 配置 / 权限问题\n"
        "  keypulse start                 启动后台采集\n"
        "  keypulse stop                  停止后台采集\n"
        "  keypulse healthcheck           运行健康检查\n"
        "  keypulse daily run             生成今天日报\n"
        "  keypulse weekly run            生成本周周报\n"
        "  keypulse obsidian sync         同步到 Obsidian\n"
        "  keypulse search \"<关键词>\"     搜索历史活动\n\n"
        "完整命令列表见下方 Commands。\n\n"
        "> 语言：中文（设置 KEYPULSE_LANG=en 切英文）",
        "KeyPulse — personal activity tracking and daily/weekly reporting.\n\n"
        "\b\n"
        "Common commands:\n"
        "  keypulse status                Show daemon status and runtime counters\n"
        "  keypulse doctor                Diagnose install, config, and permission issues\n"
        "  keypulse start                 Start background capture daemon\n"
        "  keypulse stop                  Stop background capture daemon\n"
        "  keypulse healthcheck           Run health diagnostics and emit JSON\n"
        "  keypulse daily run             Generate today's daily Markdown report\n"
        "  keypulse weekly run            Generate this week's weekly report\n"
        "  keypulse obsidian sync         Sync notes into your Obsidian vault\n"
        "  keypulse search \"<keyword>\"    Search historical activity records\n\n"
        "See Commands below for the full command list.\n\n"
        "> Language: English (set KEYPULSE_LANG=zh to switch to Chinese)"
    )
)
def main():
    pass


@main.command(help=T("首次启动配置（工作类型/区域/节奏/触发/视角）", "Initial setup wizard (work type/region/rhythm/trigger/style)."))
@click.option("--force", is_flag=True, help=T("覆盖已有 profile", "Overwrite existing profile"))
def setup(force):
    """Interactive setup."""
    profile_path = Path.home() / ".keypulse" / "profile.toml"
    if (not force) and (not is_first_run(profile_path)):
        click.echo(f"profile already exists: {profile_path} (use --force to overwrite)")
        return

    answers_map: dict[str, str] = {}
    while True:
        answers_map = {}
        for item in QUESTIONS:
            key = str(item["key"])
            options = [str(opt) for opt in item["options"]]
            default = str(item["default"])
            choice = click.Choice(options, case_sensitive=False)
            value = click.prompt(str(item["question"]), type=choice, default=default, show_choices=True)
            answers_map[key] = str(value)

        errors = validate_answers(answers_map)
        if not errors:
            break
        for err in errors:
            click.secho(err, fg="red", err=True)
        click.echo("输入不合法，请重试。")

    religion = answers_map.get("religion", "")
    if religion == "unspecified":
        religion = ""
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    answers = OnboardingAnswers(
        work_type=answers_map["work_type"],
        region=answers_map["region"],
        work_mode=answers_map["work_mode"],
        weekly_trigger=answers_map["weekly_trigger"],
        weekly_style=answers_map["weekly_style"],
        religion=religion,
        created_at=created_at,
    )
    write_profile(answers, profile_path)
    click.echo(f"✓ profile saved to {profile_path}")


@main.command(name="mark-holiday", help=T("手动标注某周为假期，影响周报模板。", "Mark a week as holiday so weekly template/output adjusts accordingly."))
@click.argument("week")
@click.option("--reason", required=True, help=T("假期原因说明", "Reason for the holiday marker"))
@click.option("--region", default=None, help=T("地区标识（可选）", "Region hint (optional)"))
def mark_holiday(week, reason, region):
    """Mark holiday metadata."""
    raw_week = str(week).strip()
    iso_week = raw_week
    short_match = re.fullmatch(r"[Ww](\d{1,2})", raw_week)
    full_match = re.fullmatch(r"(\d{4})-[Ww](\d{1,2})", raw_week)
    if short_match:
        iso_week = f"{datetime.now().year}-W{int(short_match.group(1)):02d}"
    elif full_match:
        iso_week = f"{int(full_match.group(1)):04d}-W{int(full_match.group(2)):02d}"
    else:
        raise click.UsageError("week must be W19 or YYYY-W19")

    marked_path = Path.home() / ".keypulse" / "marked-holidays.json"
    marked_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, object] = {"weeks": {}}
    if marked_path.exists():
        try:
            payload = json.loads(marked_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise click.ClickException(f"failed to read {marked_path}: {exc}") from exc
        if not isinstance(payload, dict):
            payload = {"weeks": {}}
    weeks = payload.get("weeks")
    if not isinstance(weeks, dict):
        weeks = {}
        payload["weeks"] = weeks

    marked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    weeks[iso_week] = {"reason": reason, "region": region, "marked_at": marked_at}

    marked_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"✓ holiday marked: {iso_week}")


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
@click.option("--skip-sink-detect", is_flag=True, default=False, help="Skip automatic sink detection")
@click.option("--plain", is_flag=True, default=False, help="Plain text output")
def install_init(no_launchd, no_load, force_config, force_launchd, skip_sink_detect, plain):
    """Initialize runtime."""
    if sys.platform != "darwin":
        raise click.ClickException("KeyPulse install init is currently macOS-only.")

    data_dir = get_data_dir()
    logs_dir = _install_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    config_status = _write_default_user_config(force=force_config)
    cfg = Config.load()
    init_db(cfg.db_path_expanded)

    sink_status = "skipped"
    if not skip_sink_detect:
        try:
            sink = resolve_active_sink(cfg, persist=True)
            sink_status = f"{sink.kind}:{sink.output_dir}" if sink is not None else "none"
        except Exception as exc:
            sink_status = f"failed:{type(exc).__name__}:{exc}"

    launchd_results: list[tuple[str, Path, str, bool]] = []
    if not no_launchd:
        launchd_results = _install_launchd_jobs(load=not no_load, force=force_launchd)

    if plain:
        print(f"data_dir={data_dir}")
        print(f"logs_dir={logs_dir}")
        print(f"config={config_status}:{get_config_path()}")
        print(f"db_path={cfg.db_path_expanded}")
        print(f"sink={sink_status}")
        for label, path, status_text, loaded in launchd_results:
            print(f"launchd.{label}={status_text},path={path},loaded={loaded}")
        return

    console.print("[green]KeyPulse runtime initialized.[/green]")
    console.print(f"Data: {data_dir}")
    console.print(f"Logs: {logs_dir}")
    console.print(f"Config: {get_config_path()} ({config_status})")
    console.print(f"Database: {cfg.db_path_expanded}")
    console.print(f"Sink: {sink_status}")
    if launchd_results:
        _echo_launchd_results(launchd_results)
    console.print("")
    console.print("Next steps:")
    console.print("  keypulse setup")
    console.print("  keypulse model setup")
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
    try:
        sink = resolve_active_sink(cfg, persist=False)
        checks["sink"] = (True, f"{sink.kind}:{sink.output_dir}")
    except Exception as exc:
        checks["sink"] = (False, f"{type(exc).__name__}:{exc}")
    checks["model"] = (not _model_backends_need_setup(cfg), cfg.model.active_profile)

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
    for label in _LAUNCHD_LABELS:
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
            Path(cfg.integration.state_path).expanduser(),
            Path(cfg.model.state_path).expanduser(),
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
    _warn_if_model_backends_need_setup(cfg)
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
    _warn_if_model_backends_need_setup(cfg)
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

@main.command(name="search", help=T("搜索历史活动。", "Search historical activity."))
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
# 13. OBSIDIAN
# ═════════════════════════════════════════════════════════════════════════════

@main.group(help=T("管理 Obsidian 导出桥接。", "Manage the Obsidian export bridge."))
def obsidian():
    """Obsidian group."""
    pass


def _resolve_obsidian_date(date: Optional[str], yesterday: bool) -> str:
    if yesterday:
        return _parse_date("yesterday")
    if date is None:
        return _parse_date("today")
    return _parse_date(date)


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
        wiki_link_mode=getattr(getattr(cfg, "obsidian", None), "wiki_link_mode", "relative"),
        humanize_titles=getattr(getattr(cfg, "obsidian", None), "humanize_titles", False),
    )
    try:
        ran = run_daily_after_obsidian_sync(date_str, db_path=cfg.db_path_expanded)
        if ran:
            click.echo("[daily] orchestrator ran via obsidian sync")
    except Exception as exc:
        click.echo(f"[daily] orchestrator failed via obsidian sync: {exc}", err=True)
    return len(written), target_output, sink.kind


# ═════════════════════════════════════════════════════════════════════════════
# 13.4 DAILY (PR2 orchestrator entry)
# ═════════════════════════════════════════════════════════════════════════════

@main.group(help=T("日报：生成 / 重跑 / 查看每日活动总结。", "Daily reports: generate, rerun, and inspect daily activity summaries."))
def daily():
    """Daily group."""
    pass


def _capture_legacy_daily_trigger(
    ctx: click.Context,
    param: click.Parameter,
    value: str | None,
) -> None:
    del param
    if value:
        ctx.meta["legacy_daily_trigger"] = value
    return None


def _daily_note_output_path(cfg: Config, date_str: str) -> Path:
    sink = resolve_active_sink(cfg, persist=False)
    path = sink.output_dir / "Daily" / f"{date_str}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _render_daily_fallback(cfg: Config, date_str: str, *, no_llm: bool) -> Path:
    payload = read_daily_summary(date_str) or {}
    gateway = None if no_llm else load_model_gateway(cfg)
    report = render_daily_markdown(
        date=date_str,
        topics=payload.get("topics") if isinstance(payload, dict) else [],
        events=payload.get("events") if isinstance(payload, dict) else [],
        unanchored=payload.get("unanchored") if isinstance(payload, dict) else [],
        topic_snapshot=payload.get("topic_status_snapshot") if isinstance(payload, dict) else {},
        model_gateway=gateway,
    )
    target = _daily_note_output_path(cfg, date_str)
    atomic_write_text(target, report)
    return target


@daily.command(
    "run",
    help=T(
        "生成指定日期的日报（默认今天，全量重算）。\n\n"
        "\b\n"
        "会生成一份 Markdown 日报，写入你配置的 Obsidian vault。\n"
        "内容包含：当天涉及的主题、关键决策、完成/交付事项（shipped）。\n\n"
        "\b\n"
        "示例：\n"
        "  keypulse daily run                       # 生成今天日报\n"
        "  keypulse daily run --date yesterday      # 重跑昨天日报\n"
        "  keypulse daily run --date 2026-05-18     # 重跑指定日期\n"
        "  keypulse daily run --date -3             # 重跑 3 天前\n"
        "  keypulse daily run --incremental         # 增量模式（夜间补跑用）\n",
        "Generate a daily report for the given date (defaults to today, full rebuild).\n\n"
        "\b\n"
        "This command creates a Markdown daily report and writes it to your configured Obsidian vault.\n"
        "The report summarizes what you worked on that day: active topics, decisions made, and what got shipped.\n\n"
        "\b\n"
        "When to use:\n"
        "  - Default usage: `keypulse daily run` for today's report.\n"
        "  - Backfill: rerun yesterday or any specific day if you missed a run.\n"
        "  - Incremental: nightly catch-up mode that only processes new events since the last run.\n\n"
        "\b\n"
        "Examples:\n"
        "  keypulse daily run                          # Generate today's report\n"
        "  keypulse daily run --date yesterday         # Re-run yesterday's report\n"
        "  keypulse daily run --date 2026-05-18        # Re-run a specific date\n"
        "  keypulse daily run --date -3                # Re-run 3 days ago\n"
        "  keypulse daily run --incremental            # Incremental mode (only process new events)\n"
    ),
)
@click.option("--date", "date_str", default="today", help=DATE_HELP_TEXT_SHORT)
@click.option(
    "--incremental",
    is_flag=True,
    default=False,
    help=T("增量模式：只处理上次 run 之后的新事件（夜间补跑用）。默认全量重算。", "Incremental mode: only process events since last run (for nightly catch-up). Default is full rebuild."),
)
@click.option("--mock-llm", is_flag=True, default=False, help=T("开发用：用 MOCK_LLM=1 跳过真实 LLM 调用", "Dev only: skip real LLM calls (MOCK_LLM=1)"))
@click.option(
    "--trigger",
    type=click.Choice(["18:00", "23:30"]),
    default=None,
    hidden=True,
    expose_value=False,
    callback=_capture_legacy_daily_trigger,
)
def daily_run(date_str, incremental, mock_llm):
    """Daily run."""
    date_str = _parse_date(date_str)
    cfg = get_config()
    require_db(cfg)
    trigger = "23:30" if incremental else "18:00"
    legacy_trigger = click.get_current_context().meta.get("legacy_daily_trigger")
    if legacy_trigger in {"18:00", "23:30"}:
        trigger = legacy_trigger
        click.echo("⚠️  `--trigger` 已弃用，请改用 `--incremental`。", err=True)

    previous_mock = os.environ.get("MOCK_LLM")
    if mock_llm:
        os.environ["MOCK_LLM"] = "1"

    try:
        summary = run_daily(date_str, trigger=trigger)
        click.echo(
            "daily_run=ok "
            f"date={summary.date} trigger={summary.trigger} events={summary.processed_count} "
            f"clusters={summary.cluster_count} skipped={summary.skipped}"
        )
        click.echo(f"daily_path={summary.daily_path}")
        click.echo(f"daily_summary={summary.summary_path}")
        if summary.topic_diffs:
            click.echo("topic_diffs=" + ",".join(summary.topic_diffs))
    except (DailyOrchestratorError, LLMCallError, ValueError, OSError) as exc:
        fallback_path = _render_daily_fallback(cfg, date_str, no_llm=mock_llm)
        click.echo(f"daily_run=fallback date={date_str} trigger={trigger} reason={type(exc).__name__}:{exc}")
        click.echo(f"fallback_daily_path={fallback_path}")
    finally:
        if mock_llm:
            if previous_mock is None:
                os.environ.pop("MOCK_LLM", None)
            else:
                os.environ["MOCK_LLM"] = previous_mock


# ═════════════════════════════════════════════════════════════════════════════
# 13.3b EVAL — 把 validator 包成一行命令，列 fail case + 出 score
# ═════════════════════════════════════════════════════════════════════════════

@main.group(hidden=True, help=T("评估日报/周报输出质量（隐藏命令）。", "Evaluate daily/weekly output quality (hidden commands)."))
def eval():
    """Eval group."""
    pass


@eval.command("daily", help=T("对单个 daily.md 打分并列出失败项。", "Score one daily.md and list failing checks."))
@click.option("--candidate", "candidate", required=True, type=click.Path(exists=True), help=T("待评 daily.md 路径", "Path to candidate daily.md"))
@click.option("--summary", "summary_path", type=click.Path(exists=True), default=None,
              help=T("对应 daily-summary JSON 路径（可选，提供后会跑数据层断言）", "Matching daily-summary JSON path (optional; enables data-layer assertions)"))
@click.option("--fail-only", is_flag=True, default=False, help=T("只列 fail case 不打印 banner", "Only list failed checks without banner"))
def eval_daily(candidate, summary_path, fail_only):
    """Eval daily."""
    import json as _json
    from pathlib import Path as _Path

    from keypulse.pipeline.daily_validator import quick_score, validate_daily_output

    md = _Path(candidate).read_text(encoding="utf-8")
    summary = None
    if summary_path:
        summary = _json.loads(_Path(summary_path).read_text(encoding="utf-8"))

    failures = validate_daily_output(rendered_markdown=md, daily_summary=summary)
    score = quick_score(failures)
    errors = [f for f in failures if f.severity == "error"]
    warns = [f for f in failures if f.severity == "warn"]

    if not fail_only:
        click.echo(f"=== eval daily: {candidate} ===")
        click.echo(f"score={score}/100  errors={len(errors)}  warns={len(warns)}")
        if summary_path:
            click.echo(f"data layer: ON (summary={summary_path})")
        else:
            click.echo("data layer: OFF (only rendered markdown checks)")
        click.echo("")

    if not failures:
        click.echo("✅ 全部通过，无 fail case")
        return

    for f in failures:
        sev_tag = "❌" if f.severity == "error" else "⚠️"
        click.echo(f"{sev_tag} [{f.rule}] {f.message}")
        click.echo(f"   @ {f.location}")

    if errors:
        raise SystemExit(1)


@eval.command("weekly", help=T("对单个 weekly.md 打分并列出失败项。", "Score one weekly.md and list failing checks."))
@click.option("--candidate", "candidate", required=True, type=click.Path(exists=True), help=T("待评 weekly.md 路径", "Path to candidate weekly.md"))
@click.option("--style", "style", type=click.Choice(["plain", "exec"]), default="plain", show_default=True)
@click.option("--dailies-dir", "dailies_dir", type=click.Path(exists=True), default=None,
              help=T("本周 daily.md 所在目录，用于客观性溯源（可选）", "Directory of daily.md files for this week (optional; used for objectivity checks)"))
@click.option("--fail-only", is_flag=True, default=False)
def eval_weekly(candidate, style, dailies_dir, fail_only):
    """Eval weekly."""
    from pathlib import Path as _Path

    from keypulse.pipeline.weekly_validator import quick_score, validate_weekly_output

    md = _Path(candidate).read_text(encoding="utf-8")
    dailies_corpus = ""
    if dailies_dir:
        for daily_file in sorted(_Path(dailies_dir).glob("2026-*.md")):
            dailies_corpus += daily_file.read_text(encoding="utf-8") + "\n"

    failures = validate_weekly_output(
        style=style,
        rendered_markdown=md,
        dailies_corpus=dailies_corpus,
        hud_input_dates=[],
    )
    if not dailies_dir:
        # 没 corpus 时 validator 把每个数字都判 unverified，会淹没真实问题；CLI 层过滤。
        failures = [f for f in failures if f.field != "objectivity"]
    score = quick_score(failures)

    if not fail_only:
        click.echo(f"=== eval weekly: {candidate} (style={style}) ===")
        click.echo(f"score={score}/100  failures={len(failures)}")
        if dailies_dir:
            click.echo(f"data layer: ON (dailies={dailies_dir})")
        else:
            click.echo("data layer: OFF (objectivity check 已跳过，传 --dailies-dir 启用)")
        click.echo("")

    if not failures:
        click.echo("✅ 全部通过，无 fail case")
        return

    for f in failures:
        click.echo(f"❌ [{f.field}/{f.rule}] {f.detail}")

    raise SystemExit(1)


@eval.command("skill", help=T("评估 skill 提案（占位，暂未实现）。", "Evaluate skill proposal (placeholder, not implemented yet)."))
def eval_skill():
    """Eval skill placeholder."""
    click.echo("skill propose eval 暂未实现。前置依赖：")
    click.echo("  1. V0 hello world 跑通（docs/skill-v0-plan.md §5）")
    click.echo("  2. 至少 2-3 个 skill 候选历史样本")
    click.echo("  3. 候选独特性 / 历史冲突 / 用户保留率拟合三个评估维度的 baseline")
    click.echo("解锁后此命令格式将是：keypulse eval skill --candidate <propose.md>")
    raise SystemExit(2)


# ═════════════════════════════════════════════════════════════════════════════
# 13.4 WEEKLY (PR3 orchestrator entry)
# ═════════════════════════════════════════════════════════════════════════════

@main.group(help=T("周报：生成本周或指定周的总结报告。", "Weekly reports: generate summary reports for this week or a specific week."))
def weekly():
    """Weekly group."""
    pass


@weekly.command("run", help=T("生成指定周的周报。", "Generate a weekly report for the selected week."))
@click.option("--week", "week_str", default="this", show_default=True, help=WEEK_HELP_TEXT)
@click.option("--style", "style", type=click.Choice(["plain", "exec"]), default="exec", show_default=True)
@click.option("--mock-llm", is_flag=True, default=False, help=T("开发用：用 MOCK_LLM=1 跳过真实 LLM 调用", "Dev only: use MOCK_LLM=1 stub gateway"))
def weekly_run(week_str, style, mock_llm):
    """Weekly run."""
    week_str = _parse_week(week_str)
    os.environ["HOME"] = str(Path.home())
    cfg = get_config()
    require_db(cfg)

    previous_mock = os.environ.get("MOCK_LLM")
    if mock_llm:
        os.environ["MOCK_LLM"] = "1"

    try:
        weekly_path = run_weekly(week_str, style=style)
        if weekly_path:
            outcome_raw = get_state("weekly_last_outcome") or ""
            outcome = {}
            try:
                outcome = json.loads(outcome_raw) if outcome_raw else {}
            except json.JSONDecodeError:
                outcome = {}

            if isinstance(outcome, dict) and outcome.get("outcome") == "partial":
                count = int(outcome.get("validator_failures") or 0)
                click.echo(f"weekly_run=partial week={week_str} reason=validator_failures count={count}")
            else:
                click.echo(f"weekly_run=ok week={week_str}")
            click.echo(f"weekly_path={weekly_path}")
        else:
            click.echo(f"weekly_run=skipped week={week_str} reason=insufficient_daily_data")
    except (WeeklyOrchestratorError, LLMCallError, ValueError, OSError) as exc:
        click.echo(f"weekly_run=failed week={week_str} reason={type(exc).__name__}:{exc}")
        sys.exit(1)
    finally:
        if mock_llm:
            if previous_mock is None:
                os.environ.pop("MOCK_LLM", None)
            else:
                os.environ["MOCK_LLM"] = previous_mock


# ═════════════════════════════════════════════════════════════════════════════
# 13.5 SINKS
# ═════════════════════════════════════════════════════════════════════════════

@main.group(help=T("管理自动 sink 发现。", "Manage automatic sink discovery."))
def sinks():
    """Sinks group."""
    pass


@sinks.command("detect", help=T("检测当前活跃 sink，可选持久化绑定。", "Detect the active sink and optionally persist binding."))
@click.option("--apply", "apply_binding", is_flag=True, default=False, help=T("保存检测到的 sink 绑定", "Persist the detected sink binding"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def sinks_detect(apply_binding, plain):
    """Detect sink."""
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


@sinks.command("status", help=T("查看当前 sink 绑定。", "Show active sink binding."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def sinks_status(plain):
    """Sink status."""
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


@obsidian.command("sync", help=T("导出/同步某天数据到 Obsidian。", "Export/sync one day's data into Obsidian."))
@click.option(
    "--incremental",
    is_flag=True,
    default=False,
    help=T("增量追加模式：只添加新事件，不重渲染叙事内容。", "Incremental append mode: only add new events, do not re-render narrative."),
)
@click.option("--yesterday", is_flag=True, default=False, help=T("导出昨天的数据（全量同步）", "Export yesterday's data (full sync)"))
@click.option("--date", default=None, help=DATE_HELP_TEXT)
@click.option("--output", default=None, help=T("覆盖 vault 路径", "Override vault path"))
@click.option("--vault-name", default=None, help=T("覆盖 vault 名称", "Override vault name"))
def obsidian_sync(date, yesterday, incremental, output, vault_name):
    """Obsidian sync."""
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
        date_str = _parse_date("today")
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

@main.group(help=T("检查与操作信息流水线。", "Inspect and operate the information pipeline."))
def pipeline():
    """Pipeline group."""
    pass


@pipeline.command("sync", hidden=True, help=T("执行统一 daily sync 路径。", "Run unified daily sync path."))
@click.option("--date", default=None, help=DATE_HELP_TEXT)
@click.option("--yesterday", is_flag=True, default=False, help=T("同步昨天的数据", "Sync yesterday's data"))
@click.option("--output", default=None, help=T("覆盖 vault 路径", "Override vault path"))
@click.option("--vault-name", default=None, help=T("覆盖 vault 名称", "Override vault name"))
def pipeline_sync(date, yesterday, output, vault_name):
    """Pipeline sync."""
    cfg = get_config()
    require_db(cfg)

    date_str = _resolve_obsidian_date(date, yesterday)
    written, target_output, sink_kind = _sync_obsidian_bundle(cfg, date_str, output=output, vault_name=vault_name)
    print(f"pipeline_sync=ok date={date_str} sink={sink_kind} output={target_output} written={written}")


@pipeline.command("draft", hidden=True, help=T("渲染 daily 草稿。", "Render daily draft through unified renderer."))
@click.option("--date", default=None, help=DATE_HELP_TEXT)
@click.option("--yesterday", is_flag=True, default=False, help=T("构建昨天的草稿", "Build yesterday's draft"))
@click.option("--output", default=None, help=T("将草稿写入文件而不是输出到 stdout", "Write the draft to a file instead of stdout"))
def pipeline_draft(date, yesterday, output):
    """Pipeline draft."""
    cfg = get_config()
    require_db(cfg)

    date_str = _resolve_obsidian_date(date, yesterday)
    payload = read_daily_summary(date_str) or {}
    body = render_daily_markdown(
        date=date_str,
        topics=payload.get("topics") if isinstance(payload, dict) else [],
        events=payload.get("events") if isinstance(payload, dict) else [],
        unanchored=payload.get("unanchored") if isinstance(payload, dict) else [],
        topic_snapshot=payload.get("topic_status_snapshot") if isinstance(payload, dict) else {},
        model_gateway=load_model_gateway(cfg) if hasattr(cfg, "model") else None,
    )

    if output:
        Path(output).write_text(body, encoding="utf-8")
        console.print(f"[green]Draft written to {output}[/green]")
    else:
        console.print(body)


@pipeline.group(help=T("记录与查看流水线反馈。", "Record and inspect pipeline feedback."))
def feedback():
    """Feedback group."""
    pass


@feedback.command("add", help=T("追加一条反馈事件。", "Append one feedback event."))
@click.option("--kind", required=True, help=T("反馈类型，例如 promote 或 demote", "Feedback kind, such as promote or demote"))
@click.option("--target", required=True, help=T("目标主题、事件或草稿", "Target topic, event, or draft"))
@click.option("--note", required=True, help=T("简短反馈说明", "Short feedback note"))
def feedback_add(kind, target, note):
    """Add feedback."""
    cfg = get_config()
    path = Path(cfg.pipeline.feedback_path).expanduser()
    append_feedback_event(path, FeedbackEvent(kind=kind, target=target, note=note))
    console.print(f"[green]Recorded feedback for {target}[/green]")


@feedback.command("list", help=T("列出反馈事件。", "List recorded feedback events."))
@click.option("--path", default=None, help=T("覆盖反馈日志路径", "Override feedback log path"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def feedback_list(path, plain):
    """List feedback."""
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


@feedback.command("refine", hidden=True, help=T("记录主题优化指令。", "Persist a theme refinement instruction."))
@click.option("--theme", "theme_name", required=True, help=T("要优化的主题名", "Theme name to refine"))
@click.option("--instruction", required=True, help=T("优化指令", "Refinement instruction"))
@click.option("--state-path", default=None, help=T("覆盖主题状态路径", "Override theme state path"))
def feedback_refine(theme_name, instruction, state_path):
    """Refine feedback theme."""
    result = record_theme_feedback(state_path, theme_name=theme_name, instruction=instruction)
    console.print(f"[green]{result['theme_name']} v{result['version']}[/green]")


@feedback.command("status", hidden=True, help=T("查看当前主题画像。", "Show active theme profile."))
@click.option("--state-path", default=None, help=T("覆盖主题状态路径", "Override theme state path"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def feedback_status(state_path, plain):
    """Feedback status."""
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


@dev.group("eval", help=T("评估日报/周报输出（开发入口）。", "Evaluate daily/weekly outputs (dev entry)."))
def dev_eval():
    """Dev eval group."""
    pass


@dev_eval.command("daily", help=T("评估 daily.md。", "Evaluate daily.md."))
@click.option("--candidate", "candidate", required=True, type=click.Path(exists=True), help=T("待评 daily.md 路径", "Path to candidate daily.md"))
@click.option("--summary", "summary_path", type=click.Path(exists=True), default=None,
              help=T("对应 daily-summary JSON 路径（可选，提供后会跑数据层断言）", "Matching daily-summary JSON path (optional; enables data-layer assertions)"))
@click.option("--fail-only", is_flag=True, default=False, help=T("只列 fail case 不打印 banner", "Only list failed checks without banner"))
def dev_eval_daily(candidate, summary_path, fail_only):
    """Dev eval daily."""
    return eval_daily.callback(candidate, summary_path, fail_only)


@dev_eval.command("weekly", help=T("评估 weekly.md。", "Evaluate weekly.md."))
@click.option("--candidate", "candidate", required=True, type=click.Path(exists=True), help=T("待评 weekly.md 路径", "Path to candidate weekly.md"))
@click.option("--style", "style", type=click.Choice(["plain", "exec"]), default="plain", show_default=True)
@click.option("--dailies-dir", "dailies_dir", type=click.Path(exists=True), default=None,
              help=T("本周 daily.md 所在目录，用于客观性溯源（可选）", "Directory of daily.md files for this week (optional; used for objectivity checks)"))
@click.option("--fail-only", is_flag=True, default=False)
def dev_eval_weekly(candidate, style, dailies_dir, fail_only):
    """Dev eval weekly."""
    return eval_weekly.callback(candidate, style, dailies_dir, fail_only)


@dev_eval.command("skill", help=T("评估 skill 提案（占位）。", "Evaluate skill proposal (placeholder)."))
def dev_eval_skill():
    """Dev eval skill placeholder."""
    return eval_skill.callback()


@dev.group("pipeline", help=T("信息流水线命令（开发入口）。", "Pipeline commands (dev entry)."))
def dev_pipeline():
    """Dev pipeline group."""
    pass


@dev_pipeline.command("sync", help=T("执行统一 daily sync 路径。", "Run unified daily sync path."))
@click.option("--date", default=None, help=DATE_HELP_TEXT)
@click.option("--yesterday", is_flag=True, default=False, help=T("同步昨天的数据", "Sync yesterday's data"))
@click.option("--output", default=None, help=T("覆盖 vault 路径", "Override vault path"))
@click.option("--vault-name", default=None, help=T("覆盖 vault 名称", "Override vault name"))
def dev_pipeline_sync(date, yesterday, output, vault_name):
    """Dev pipeline sync."""
    return pipeline_sync.callback(date, yesterday, output, vault_name)


@dev_pipeline.command("draft", help=T("渲染 daily 草稿。", "Render daily draft."))
@click.option("--date", default=None, help=DATE_HELP_TEXT)
@click.option("--yesterday", is_flag=True, default=False, help=T("构建昨天的草稿", "Build yesterday's draft"))
@click.option("--output", default=None, help=T("将草稿写入文件而不是输出到 stdout", "Write the draft to a file instead of stdout"))
def dev_pipeline_draft(date, yesterday, output):
    """Dev pipeline draft."""
    return pipeline_draft.callback(date, yesterday, output)


@dev.group("feedback", help=T("流水线反馈命令（开发入口）。", "Pipeline feedback commands (dev entry)."))
def dev_feedback():
    """Dev feedback group."""
    pass


@dev_feedback.command("refine", help=T("记录主题优化指令。", "Persist a theme refinement instruction."))
@click.option("--theme", "theme_name", required=True, help=T("要优化的主题名", "Theme name to refine"))
@click.option("--instruction", required=True, help=T("优化指令", "Refinement instruction"))
@click.option("--state-path", default=None, help=T("覆盖主题状态路径", "Override theme state path"))
def dev_feedback_refine(theme_name, instruction, state_path):
    """Dev feedback refine."""
    return feedback_refine.callback(theme_name, instruction, state_path)


@dev_feedback.command("status", help=T("查看当前主题画像。", "Show active theme profile."))
@click.option("--state-path", default=None, help=T("覆盖主题状态路径", "Override theme state path"))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def dev_feedback_status(state_path, plain):
    """Dev feedback status."""
    return feedback_status.callback(state_path, plain)


# ═════════════════════════════════════════════════════════════════════════════
# 14. EXPORT
# ═════════════════════════════════════════════════════════════════════════════

@main.command(help=T("导出活动数据。", "Export activity data."))
@click.option("--format", type=click.Choice(["json", "csv", "md", "obsidian"]), default="json",
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
            date_str=parsed_date,
            vault_name=cfg.obsidian.vault_name,
            model_gateway=gateway,
            wiki_link_mode=getattr(getattr(cfg, "obsidian", None), "wiki_link_mode", "relative"),
            humanize_titles=getattr(getattr(cfg, "obsidian", None), "humanize_titles", False),
        )
        console.print(f"[green]Exported {len(written)} notes to {target_output}[/green]")
        return
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


@main.command(name="cost", help=T("查看 ~/.keypulse/cost.jsonl 的 LLM 成本摘要。", "Show LLM cost summary from ~/.keypulse/cost.jsonl."))
@click.option("--week", "week_text", default=None, help=T("ISO 周，例如 2026-W18", "ISO week, e.g. 2026-W18"))
@click.option("--month", "month_text", default=None, help=T("月份，例如 2026-05", "Month, e.g. 2026-05"))
def cost_report(week_text, month_text):
    """Cost report."""
    if week_text and month_text:
        raise click.UsageError("Use either --week or --month, not both")

    cost_path = get_data_dir() / "cost.jsonl"
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end_year = month_start.year + 1 if month_start.month == 12 else month_start.year
    month_end_month = 1 if month_start.month == 12 else month_start.month + 1
    month_end = month_start.replace(year=month_end_year, month=month_end_month)
    week_start = datetime.fromisocalendar(now.isocalendar().year, now.isocalendar().week, 1).replace(tzinfo=timezone.utc)
    week_end = week_start + timedelta(days=7)

    if month_text:
        start, end = _month_bounds(month_text)
        total, grouped = _aggregate_cost(cost_path, start, end)
        print(f"月份({month_text}): ${total:.2f}")
    elif week_text:
        start, end = _week_bounds(week_text)
        total, grouped = _aggregate_cost(cost_path, start, end)
        print(f"周({week_text}): ${total:.2f}")
    else:
        this_month, month_grouped = _aggregate_cost(cost_path, month_start, month_end)
        this_week, _week_grouped = _aggregate_cost(cost_path, week_start, week_end)
        print(f"本月({month_start.strftime('%Y-%m')}): ${this_month:.2f}")
        print(f"本周({now.isocalendar().year}-W{now.isocalendar().week:02d}): ${this_week:.2f}")
        grouped = month_grouped

    print("按 capability:")
    for capability, metrics in sorted(grouped.items(), key=lambda item: (-float(item[1]["cost_usd"]), item[0])):
        cost_value = float(metrics["cost_usd"])
        calls = int(metrics["calls"])
        cache_hits = int(metrics["cache_hits"])
        print(f"  {capability}  ${cost_value:.2f}  ({calls} calls, {cache_hits} cache hits)")


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
        print(f"llm_tier={cfg.llm.tier}")
        print(f"llm_provider={cfg.llm.provider}")
        print(f"llm_monthly_budget_usd={cfg.llm.monthly_budget_usd}")
        print(f"llm_local_ollama_url={cfg.llm.local_ollama_url}")
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
# ═════════════════════════════════════════════════════════════════════════════
# 17. MODEL (subgroup)
# ═════════════════════════════════════════════════════════════════════════════

@main.group(help=T("管理模型网关配置。", "Manage model gateway profiles."))
def model():
    """Model group."""
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


@model.command("status", help=T("查看模型 profile、后端健康与回退状态。", "Show model profile, backend health, and fallback state."))
@click.option("--plain", is_flag=True, default=False, help=PLAIN_HELP)
def model_status(plain):
    """Model status."""
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


@model.command("setup", help=T("交互式配置云端/本地模型后端与 keychain。", "Interactive setup for cloud/local model backends with keychain storage."))
def model_setup():
    """Model setup."""
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


main.add_command(model_setup, "model-setup")


@model.command("use", help=T("切换并持久化当前模型 profile。", "Persist the active model profile."))
@click.argument("profile")
def model_use(profile):
    """Use model profile."""
    cfg = get_config()
    gateway = load_model_gateway(cfg)
    try:
        gateway.use_profile(profile)
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    console.print(f"[green]Model profile set to {profile}[/green]")


@model.command("test", help=T("测试当前选择的模型后端。", "Test the selected model backend."))
def model_test():
    """Model backend test."""
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


@rules.command("add", help=T("新增隐私策略。", "Add a new privacy policy."))
@click.option("--scope-type", required=True, help=T("范围类型（app、window、source、content）", "Scope type (app, window, source, content)"))
@click.option("--scope-value", required=True, help=T("范围值（例如 'Safari'、'password'）", "Scope value (e.g., 'Safari', 'password')"))
@click.option("--mode", required=True, help=T("模式（allow、deny、metadata-only、redact、truncate）", "Mode (allow, deny, metadata-only, redact, truncate)"))
@click.option("--priority", type=int, default=100, help=T("优先级（值越小优先级越高）", "Priority (lower = higher priority)"))
def rules_add(scope_type, scope_value, mode, priority):
    """Add rule."""
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


@maintenance.command(name="scrub-secrets", help=T("扫描并脱敏数据库与 vault 内的敏感信息。", "Scan and redact secrets from database and vault."))
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
