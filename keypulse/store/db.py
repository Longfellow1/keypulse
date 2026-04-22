import sqlite3
import threading
from pathlib import Path
from keypulse.store.migrations import run_migrations

_local = threading.local()

_db_path: Path | None = None


def init_db(db_path: Path):
    global _db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if _db_path is not None and _db_path != db_path and hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None
    _db_path = db_path
    conn = _get_conn()
    run_migrations(conn)


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        if _db_path is None:
            raise RuntimeError("DB not initialized. Call init_db() first.")
        conn = sqlite3.connect(str(_db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return _local.conn


def get_conn() -> sqlite3.Connection:
    return _get_conn()


def close():
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None
