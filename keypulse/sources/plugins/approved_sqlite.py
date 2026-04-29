from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from keypulse.sources.approval import ApprovalStore
from keypulse.sources.cleaning.file_whitelist import is_blocked_sqlite
from keypulse.sources.types import FIELD_HEURISTICS, DataSource, DataSourceInstance, SemanticEvent


_EVENT_TABLE_KEYWORDS = ("messages", "chats", "history", "sessions", "conversations", "events")
_TIME_COLUMN_NAMES = {"timestamp", "ts", "created_at", "time", "date"}
_TEXT_COLUMN_NAMES = {"text", "content", "body", "message", "title"}
_ID_COLUMN_NAMES = {"id", "rowid", "uuid"}
_READ_LIMIT = 1000

_CF_EPOCH_OFFSET_S = 978_307_200
_WEBKIT_EPOCH_OFFSET_S = 11_644_473_600
_WEBKIT_EPOCH_OFFSET_US = _WEBKIT_EPOCH_OFFSET_S * 1_000_000


class ApprovedSqliteSource(DataSource):
    name = "approved_sqlite"
    privacy_tier = "yellow"
    liveness = "always"
    description = "读取用户已批准的候选 SQLite 金矿"

    def __init__(self, *, approval_store: ApprovalStore | None = None) -> None:
        self._approval_store = approval_store or ApprovalStore()

    def discover(self) -> list[DataSourceInstance]:
        try:
            approved_records = self._approval_store.list_approved()
        except Exception:
            return []

        instances: list[DataSourceInstance] = []
        for record in approved_records:
            if record.metadata.get("discoverer") != "sqlite":
                continue
            raw_path = str(record.metadata.get("path") or "").strip()
            if not raw_path:
                continue

            sqlite_path = Path(raw_path).expanduser()
            if not sqlite_path.exists() or not sqlite_path.is_file():
                continue
            blocked, _ = is_blocked_sqlite(sqlite_path)
            if blocked:
                continue

            hint_tables = _load_hint_tables(record.metadata.get("hint_tables"))
            if not hint_tables:
                hint_tables = _scan_hint_tables(sqlite_path)

            app_hint = str(record.metadata.get("app_hint") or sqlite_path.parent.name or "sqlite")
            candidate_id = record.candidate_id
            instances.append(
                DataSourceInstance(
                    plugin=self.name,
                    locator=str(sqlite_path.resolve(strict=False)),
                    label=app_hint,
                    metadata={
                        "candidate_id": candidate_id,
                        "approved_candidate_id": candidate_id,
                        "hint_tables": hint_tables,
                        "app_hint": app_hint,
                    },
                )
            )
        return instances

    def read(
        self,
        instance: DataSourceInstance,
        since: datetime,
        until: datetime,
    ) -> Iterator[SemanticEvent]:
        sqlite_path = Path(instance.locator).expanduser()
        if not sqlite_path.exists() or not sqlite_path.is_file():
            return iter(())
        blocked, _ = is_blocked_sqlite(sqlite_path)
        if blocked:
            return iter(())

        since_utc = since.astimezone(timezone.utc)
        until_utc = until.astimezone(timezone.utc)
        candidate_id = str(
            instance.metadata.get("candidate_id")
            or instance.metadata.get("approved_candidate_id")
            or "unknown"
        )
        hint_tables = _load_hint_tables(instance.metadata.get("hint_tables"))
        app_hint = str(instance.metadata.get("app_hint") or instance.label or "")

        def _iter_events() -> Iterator[SemanticEvent]:
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                try:
                    shutil.copy2(sqlite_path, tmp.name)
                except Exception:
                    return

                conn: sqlite3.Connection | None = None
                try:
                    conn = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
                    conn.execute("PRAGMA query_only=ON")
                    table = choose_event_table(conn, hint_tables)
                    if table is None:
                        return

                    columns = choose_columns(conn, table)
                    time_col = columns.get("time")
                    text_col = columns.get("text")
                    id_col = columns.get("id")
                    if not time_col:
                        return

                    selected_columns: list[str] = [time_col]
                    if text_col and text_col not in selected_columns:
                        selected_columns.append(text_col)
                    if id_col and id_col not in selected_columns:
                        selected_columns.append(id_col)

                    where_clause, params = _build_time_filter(time_col, since_utc, until_utc)
                    select_clause = ", ".join(_quote_identifier(col) for col in selected_columns)
                    query = (
                        f"SELECT {select_clause} "
                        f"FROM {_quote_identifier(table)} "
                        f"{where_clause} "
                        f"LIMIT {_READ_LIMIT}"
                    )
                    rows = conn.execute(query, params).fetchall()
                except Exception:
                    return
                finally:
                    if conn is not None:
                        conn.close()

                for idx, row in enumerate(rows, start=1):
                    payload = {selected_columns[pos]: value for pos, value in enumerate(row)}
                    parsed_time = parse_time_value(payload.get(time_col))
                    if parsed_time is None:
                        continue

                    parsed_utc = parsed_time.astimezone(timezone.utc)
                    if parsed_utc < since_utc or parsed_utc > until_utc:
                        continue

                    row_id = payload.get(id_col) if id_col else None
                    row_id_or_idx = _row_identifier(row_id, idx)
                    raw_text = payload.get(text_col) if text_col else None
                    intent_text = str(raw_text or "").strip()
                    intent = intent_text[:200] if intent_text else f"{table} row {idx}"
                    yield SemanticEvent(
                        time=parsed_utc,
                        source=self.name,
                        actor="user",
                        intent=intent,
                        artifact=f"{instance.label}:{table}:{row_id_or_idx}",
                        raw_ref=f"approved_sqlite:{candidate_id}:{table}:{row_id_or_idx}",
                        privacy_tier=self.privacy_tier,
                        metadata={
                            "approved_candidate_id": candidate_id,
                            "table": table,
                            "app_hint": app_hint,
                        },
                    )

        return _iter_events()


def choose_event_table(conn: sqlite3.Connection, hint_tables: list[str]) -> str | None:
    tables = _list_tables(conn)
    if not tables:
        return None

    hint_set = {name.lower() for name in hint_tables}
    best_table: str | None = None
    best_score = 0

    for table in tables:
        columns = _table_columns(conn, table)
        lower_columns = {name.lower() for name in columns}
        lower_table = table.lower()

        score = 0
        if lower_table in hint_set:
            score += 10
        if any(keyword in lower_table for keyword in _EVENT_TABLE_KEYWORDS):
            score += 5
        if lower_columns & _time_heuristics():
            score += 3
        if lower_columns & _text_heuristics():
            score += 5

        if score > best_score:
            best_score = score
            best_table = table

    if best_score == 0:
        return None
    return best_table


def choose_columns(conn: sqlite3.Connection, table: str) -> dict[str, str | None]:
    columns = _table_columns(conn, table)
    lowered = [(name, name.lower()) for name in columns]

    time_col = _pick_by_names(lowered, _TIME_COLUMN_NAMES)
    text_col = _pick_by_names(lowered, _TEXT_COLUMN_NAMES)
    id_col = _pick_by_names(lowered, _ID_COLUMN_NAMES)

    return {"time": time_col, "text": text_col, "id": id_col}


def parse_time_value(value: Any) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="ignore")
        except Exception:
            return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            try:
                numeric = float(text)
            except ValueError:
                return None
            return _parse_numeric_time(numeric)

    if isinstance(value, (int, float)):
        return _parse_numeric_time(float(value))

    return None


def _parse_numeric_time(v: float) -> datetime | None:
    if 1_000_000_000_000 < v < 4_000_000_000_000:
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    if 11_000_000_000_000_000 < v < 14_000_000_000_000_000:
        return datetime.fromtimestamp((v / 1_000_000) - _WEBKIT_EPOCH_OFFSET_S, tz=timezone.utc)
    if 600_000_000 < v < 1_500_000_000:
        unix_guess = datetime.fromtimestamp(v, tz=timezone.utc)
        cf_guess = datetime.fromtimestamp(v + _CF_EPOCH_OFFSET_S, tz=timezone.utc)
        if unix_guess.year < 2001 <= cf_guess.year:
            return cf_guess
    if 0 < v < 4_000_000_000:
        return datetime.fromtimestamp(v, tz=timezone.utc)
    if 600_000_000 < v < 1_500_000_000:
        return datetime.fromtimestamp(v + _CF_EPOCH_OFFSET_S, tz=timezone.utc)
    return None


def _build_time_filter(time_col: str, since: datetime, until: datetime) -> tuple[str, tuple[float | str, ...]]:
    quoted = _quote_identifier(time_col)
    since_unix = since.astimezone(timezone.utc).timestamp()
    until_unix = until.astimezone(timezone.utc).timestamp()
    since_ms = since_unix * 1000
    until_ms = until_unix * 1000
    since_webkit = since_unix * 1_000_000 + _WEBKIT_EPOCH_OFFSET_US
    until_webkit = until_unix * 1_000_000 + _WEBKIT_EPOCH_OFFSET_US
    since_cf = since_unix - _CF_EPOCH_OFFSET_S
    until_cf = until_unix - _CF_EPOCH_OFFSET_S
    since_iso = since.astimezone(timezone.utc).isoformat()
    until_iso = until.astimezone(timezone.utc).isoformat()

    clause = (
        "WHERE ("
        f"({quoted} BETWEEN ? AND ?) OR "
        f"({quoted} BETWEEN ? AND ?) OR "
        f"({quoted} BETWEEN ? AND ?) OR "
        f"({quoted} BETWEEN ? AND ?) OR "
        f"(CAST({quoted} AS TEXT) BETWEEN ? AND ?)"
        ")"
    )
    params: tuple[float | str, ...] = (
        since_unix,
        until_unix,
        since_ms,
        until_ms,
        since_webkit,
        until_webkit,
        since_cf,
        until_cf,
        since_iso,
        until_iso,
    )
    return clause, params


def _pick_by_names(columns: list[tuple[str, str]], candidates: set[str]) -> str | None:
    for original, lowered in columns:
        if lowered in candidates:
            return original
    return None


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables: list[str] = []
    for row in rows:
        if not row:
            continue
        name = row[0]
        if isinstance(name, str) and name:
            tables.append(name)
    return sorted(set(tables))


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    columns: list[str] = []
    for row in rows:
        if len(row) < 2:
            continue
        name = row[1]
        if isinstance(name, str) and name:
            columns.append(name)
    return columns


def _scan_hint_tables(path: Path) -> list[str]:
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
        tables = _list_tables(conn)
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()

    hints = [table for table in tables if any(keyword in table.lower() for keyword in _EVENT_TABLE_KEYWORDS)]
    return sorted(set(hints))


def _load_hint_tables(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("[") and raw.endswith("]"):
            try:
                loaded = json.loads(raw)
            except Exception:
                loaded = None
            if isinstance(loaded, list):
                return [str(item) for item in loaded if str(item).strip()]
        if "," in raw:
            return [part.strip() for part in raw.split(",") if part.strip()]
        return [raw]
    return []


def _time_heuristics() -> set[str]:
    return set(FIELD_HEURISTICS.get("time", set())) | _TIME_COLUMN_NAMES


def _text_heuristics() -> set[str]:
    return set(FIELD_HEURISTICS.get("intent_text", set())) | _TEXT_COLUMN_NAMES


def _row_identifier(value: Any, fallback_index: int) -> str:
    if value is None:
        return str(fallback_index)
    text = str(value).strip()
    return text or str(fallback_index)


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

