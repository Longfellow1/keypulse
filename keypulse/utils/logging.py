import logging
import json
from datetime import datetime, timezone
from pathlib import Path


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
    root = logging.getLogger("keypulse")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # File handler (JSON)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(JSONFormatter())
    root.addHandler(fh)
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"keypulse.{name}")
