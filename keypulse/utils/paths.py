from pathlib import Path


def get_data_dir() -> Path:
    d = Path.home() / ".keypulse"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_db_path() -> Path:
    return get_data_dir() / "keypulse.db"


def get_pid_path() -> Path:
    return get_data_dir() / "keypulse.pid"


def get_hud_pid_path() -> Path:
    return get_data_dir() / "keypulse-hud.pid"


def get_log_path() -> Path:
    return get_data_dir() / "keypulse.log"


def get_config_path() -> Path:
    return get_data_dir() / "config.toml"
