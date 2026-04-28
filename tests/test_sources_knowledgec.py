from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from keypulse.sources.plugins.knowledgec import KnowledgeCSource
from keypulse.sources.types import DataSourceInstance


CF_EPOCH_OFFSET_S = 978_307_200


def _to_cf_seconds(unix_seconds: float) -> float:
    return unix_seconds - CF_EPOCH_OFFSET_S


def _make_knowledgec_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE ZOBJECT (
                Z_PK INTEGER PRIMARY KEY,
                ZSTREAMNAME TEXT,
                ZSTARTDATE REAL,
                ZENDDATE REAL,
                ZVALUESTRING TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO ZOBJECT(Z_PK, ZSTREAMNAME, ZSTARTDATE, ZENDDATE, ZVALUESTRING) VALUES (?, ?, ?, ?, ?)",
            (
                101,
                "/app/usage",
                _to_cf_seconds(datetime(2026, 4, 28, 1, 0, tzinfo=timezone.utc).timestamp()),
                _to_cf_seconds(datetime(2026, 4, 28, 1, 10, tzinfo=timezone.utc).timestamp()),
                "com.tencent.xinWeChat",
            ),
        )
        conn.execute(
            "INSERT INTO ZOBJECT(Z_PK, ZSTREAMNAME, ZSTARTDATE, ZENDDATE, ZVALUESTRING) VALUES (?, ?, ?, ?, ?)",
            (
                102,
                "/app/inFocus",
                _to_cf_seconds(datetime(2026, 4, 28, 2, 0, tzinfo=timezone.utc).timestamp()),
                _to_cf_seconds(datetime(2026, 4, 28, 2, 2, tzinfo=timezone.utc).timestamp()),
                "com.apple.Safari",
            ),
        )
        conn.execute(
            "INSERT INTO ZOBJECT(Z_PK, ZSTREAMNAME, ZSTARTDATE, ZENDDATE, ZVALUESTRING) VALUES (?, ?, ?, ?, ?)",
            (
                103,
                "/notification/usage",
                _to_cf_seconds(datetime(2026, 4, 28, 3, 0, tzinfo=timezone.utc).timestamp()),
                _to_cf_seconds(datetime(2026, 4, 28, 3, 0, 3, tzinfo=timezone.utc).timestamp()),
                "Slack message",
            ),
        )
        conn.execute(
            "INSERT INTO ZOBJECT(Z_PK, ZSTREAMNAME, ZSTARTDATE, ZENDDATE, ZVALUESTRING) VALUES (?, ?, ?, ?, ?)",
            (
                104,
                "/safari/history",
                _to_cf_seconds(datetime(2026, 4, 28, 4, 0, tzinfo=timezone.utc).timestamp()),
                _to_cf_seconds(datetime(2026, 4, 28, 4, 0, 1, tzinfo=timezone.utc).timestamp()),
                "https://example.com/path?q=1",
            ),
        )
        conn.execute(
            "INSERT INTO ZOBJECT(Z_PK, ZSTREAMNAME, ZSTARTDATE, ZENDDATE, ZVALUESTRING) VALUES (?, ?, ?, ?, ?)",
            (
                105,
                "/app/usage",
                _to_cf_seconds(datetime(2026, 4, 28, 5, 0, tzinfo=timezone.utc).timestamp()),
                _to_cf_seconds(datetime(2026, 4, 28, 5, 0, 0, 500_000, tzinfo=timezone.utc).timestamp()),
                "com.apple.mail",
            ),
        )
        conn.execute(
            "INSERT INTO ZOBJECT(Z_PK, ZSTREAMNAME, ZSTARTDATE, ZENDDATE, ZVALUESTRING) VALUES (?, ?, ?, ?, ?)",
            (
                106,
                "/app/activity",
                _to_cf_seconds(datetime(2026, 4, 28, 6, 0, tzinfo=timezone.utc).timestamp()),
                _to_cf_seconds(datetime(2026, 4, 28, 6, 5, tzinfo=timezone.utc).timestamp()),
                "ignored-stream",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_knowledgec_discover(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    db_path = home / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db"
    _make_knowledgec_db(db_path)

    source = KnowledgeCSource()
    instances = source.discover()

    assert len(instances) == 1
    instance = instances[0]
    assert instance.plugin == "knowledgec"
    assert instance.locator == str(db_path)
    assert instance.label == "macOS 系统活动"
    assert instance.metadata["path"] == str(db_path)


def test_knowledgec_discover_returns_empty_when_missing(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    source = KnowledgeCSource()
    assert source.discover() == []


def test_knowledgec_discover_handles_unreadable_or_bad_schema(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    db_path = home / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE NOT_ZOBJECT (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    source = KnowledgeCSource()
    assert source.discover() == []

    def _raise_permission(_path):
        raise PermissionError("fda denied")

    monkeypatch.setattr(source, "_open_readonly_copy", _raise_permission)
    assert source.discover() == []


def test_knowledgec_read_uses_temp_copy_and_maps_events(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    db_path = home / "Library" / "Application Support" / "Knowledge" / "knowledgeC.db"
    _make_knowledgec_db(db_path)

    source = KnowledgeCSource()
    instance = DataSourceInstance(plugin="knowledgec", locator=str(db_path), label="macOS 系统活动")

    original_copy2 = __import__("shutil").copy2
    copied: list[tuple[str, str]] = []

    def tracking_copy2(src, dst, *args, **kwargs):
        copied.append((str(src), str(dst)))
        return original_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr("keypulse.sources.plugins.knowledgec.shutil.copy2", tracking_copy2)

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert copied
    assert len(events) == 4
    assert events[0].intent == "使用 xinWeChat（600s）"
    assert events[0].artifact == "app:com.tencent.xinWeChat"
    assert events[0].raw_ref == "knowledgec:/app/usage:101"
    assert events[0].metadata["duration_sec"] == 600
    assert events[1].intent == "聚焦 Safari"
    assert events[1].artifact == "app:com.apple.Safari"
    assert events[2].actor == "system"
    assert events[2].intent == "通知：Slack message"
    assert events[2].artifact == "notification:Slack message"
    assert events[3].intent == "Safari 访问：https://example.com/path?q=1"
    assert events[3].artifact == "https://example.com/path?q=1"


def test_knowledgec_read_returns_empty_on_invalid_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "broken.db"
    db_path.write_text("not a sqlite db", encoding="utf-8")

    source = KnowledgeCSource()
    instance = DataSourceInstance(plugin="knowledgec", locator=str(db_path), label="broken")

    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert events == []
