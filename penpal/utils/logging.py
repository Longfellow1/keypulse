import logging
import json
from datetime import datetime, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler


APP_LOG_MAX_BYTES = 5 * 1024 * 1024
APP_LOG_BACKUP_COUNT = 3


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging(log_path: Path, level: str = "INFO"):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("penpal")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Only the daemon writes this shared app log. CLI, HUD, and MCP processes
    # keep their normal stderr behavior, avoiding multi-process rotation races.
    for handler in tuple(root.handlers):
        if getattr(handler, "_penpal_app_log", False):
            root.removeHandler(handler)
            handler.close()

    fh = RotatingFileHandler(
        log_path,
        maxBytes=APP_LOG_MAX_BYTES,
        backupCount=APP_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh._penpal_app_log = True
    fh.setFormatter(JSONFormatter())
    root.addHandler(fh)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"penpal.{name}")
