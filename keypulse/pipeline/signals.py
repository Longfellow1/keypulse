from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from keypulse.pipeline.evidence import EvidenceUnit

_DEFAULT_EXCLUDE = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "dist", "build",
})
_MAX_DEPTH = 4
_MAX_FILES_PER_DIR = 500


@dataclass
class FsSignal:
    path: str
    basename: str
    mtime: datetime
    size: int
    ext: str


@dataclass
class BrowserSignal:
    title: str
    url: Optional[str]
    app: str
    ts: datetime


def _is_hidden(name: str) -> bool:
    return name.startswith(".")


def _should_exclude(name: str, exclude_set: frozenset[str]) -> bool:
    return name in exclude_set


def collect_filesystem_signals(
    since: datetime,
    until: datetime,
    *,
    watch_paths: list[Path],
    exclude_patterns: list[str] | None = None,
) -> list[FsSignal]:
    exclude_set = _DEFAULT_EXCLUDE | frozenset(exclude_patterns or [])

    since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    until_utc = until if until.tzinfo else until.replace(tzinfo=timezone.utc)

    results: list[FsSignal] = []

    for root_path in watch_paths:
        expanded = Path(root_path).expanduser()
        if not expanded.exists():
            continue

        for dirpath, dirnames, filenames in os.walk(str(expanded)):
            rel = Path(dirpath).relative_to(expanded)
            depth = len(rel.parts)

            # Prune excluded / hidden directories in-place
            dirnames[:] = [
                d for d in dirnames
                if not _should_exclude(d, exclude_set) and not (depth == 0 and _is_hidden(d))
            ]

            if depth >= _MAX_DEPTH:
                dirnames.clear()

            # Respect per-directory file cap
            names = filenames[:_MAX_FILES_PER_DIR]

            for name in names:
                if _is_hidden(name):
                    continue
                full = Path(dirpath) / name
                try:
                    stat = full.stat()
                except OSError:
                    continue
                mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                if since_utc <= mtime_dt <= until_utc:
                    results.append(FsSignal(
                        path=str(full),
                        basename=name,
                        mtime=mtime_dt,
                        size=stat.st_size,
                        ext=full.suffix,
                    ))

    return results


def collect_browser_signals(
    since: datetime,
    until: datetime,
    *,
    db_path: Path,
) -> list[BrowserSignal]:
    if not db_path.exists():
        return []

    since_utc = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    until_utc = until if until.tzinfo else until.replace(tzinfo=timezone.utc)

    since_str = since_utc.strftime("%Y-%m-%dT%H:%M:%S")
    until_str = until_utc.strftime("%Y-%m-%dT%H:%M:%S")

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                """
                SELECT ts_start, text, app_name, source
                FROM raw_events
                WHERE source LIKE 'browser%'
                  AND ts_start >= ?
                  AND ts_start <= ?
                ORDER BY ts_start ASC
                """,
                (since_str, until_str),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()
    except Exception:
        return []

    # Parse rows and aggregate consecutive duplicates by title
    signals: list[BrowserSignal] = []
    seen_title: str | None = None

    for ts_str, text, app_name, source in rows:
        # text column holds JSON payload; extract title best-effort
        title, url = _extract_title_url(text)
        if not title:
            continue
        app = app_name or _source_to_app(source)

        ts_dt = _parse_ts(ts_str)

        if title == seen_title:
            continue
        seen_title = title
        signals.append(BrowserSignal(title=title, url=url, app=app, ts=ts_dt))

    return signals


def _extract_title_url(text: str | None) -> tuple[str, str | None]:
    if not text:
        return "", None
    import json
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return str(data.get("title") or data.get("text") or ""), data.get("url") or None
    except (json.JSONDecodeError, TypeError):
        pass
    return str(text).strip(), None


def _source_to_app(source: str) -> str:
    mapping = {
        "browser_safari": "Safari",
        "browser_chrome": "Google Chrome",
        "browser_arc": "Arc",
        "browser_brave": "Brave Browser",
        "browser_edge": "Microsoft Edge",
    }
    return mapping.get(source, source)


def _parse_ts(ts_str: str) -> datetime:
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return datetime.min.replace(tzinfo=timezone.utc)


def enrich_unit_with_signals(
    unit: EvidenceUnit,
    *,
    fs_signals: list[FsSignal],
    browser_signals: list[BrowserSignal],
) -> EvidenceUnit:
    ts_start = unit.ts_start if unit.ts_start.tzinfo else unit.ts_start.replace(tzinfo=timezone.utc)
    ts_end = unit.ts_end if unit.ts_end.tzinfo else unit.ts_end.replace(tzinfo=timezone.utc)

    # Filter fs signals that fall within the unit time window
    matching_fs = [
        s for s in fs_signals
        if ts_start <= (s.mtime if s.mtime.tzinfo else s.mtime.replace(tzinfo=timezone.utc)) <= ts_end
    ][:3]

    # Filter browser signals that fall within the unit time window
    matching_browser = [
        s for s in browser_signals
        if ts_start <= (s.ts if s.ts.tzinfo else s.ts.replace(tzinfo=timezone.utc)) <= ts_end
    ][:3]

    what = unit.what

    if matching_fs:
        names = ", ".join(s.basename for s in matching_fs)
        what = f"{what} | external 改了 {names}"

    if matching_browser:
        titles = ", ".join(s.title for s in matching_browser)
        what = f"{what} | external 浏览了 {titles}"

    return replace(unit, what=what)
