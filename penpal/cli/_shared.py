from __future__ import annotations
from rich.console import Console
from penpal.config import Config
from penpal.store.db import init_db
from penpal.i18n import CLI_LANG as _CLI_LANG, T
from penpal.runtime import state_text


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

def require_db(cfg: Config):
    """Initialize database if not already done."""
    init_db(cfg.db_path_expanded)


def _runtime_status_line(runtime_status) -> str:
    return f"{runtime_status.code} ({state_text(runtime_status.state)})"


def _runtime_fix_command(runtime_status) -> str:
    return runtime_status.fix_command or next(
        (issue.fix_command for issue in runtime_status.issues if issue.fix_command), ""
    )
