from __future__ import annotations

import json
import re
from pathlib import Path

from keypulse.obsidian.exporter import export_obsidian, export_obsidian_incremental
from keypulse.store.db import init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event


DATE = "2026-04-22"


def _insert_event(db_path: Path, time_str: str, title: str, *, tags: str = "keypulse,incremental") -> int:
    init_db(db_path)
    event = RawEvent(
        source="manual",
        event_type="manual_save",
        ts_start=f"{DATE}T{time_str}+00:00",
        ts_end=f"{DATE}T{time_str}+00:00",
        app_name="Terminal",
        window_title=title,
        content_text=title,
        metadata_json=json.dumps({"tags": tags}, ensure_ascii=False),
        speaker="user",
        created_at=f"{DATE}T{time_str}+00:00",
    )
    return insert_raw_event(event)


def _daily_path(vault_path: Path) -> Path:
    return vault_path / "Daily" / f"{DATE}.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _section(text: str, heading: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(heading)}\n(.*?)(?=^## |\Z)", text)
    return match.group(1) if match else ""


def _event_links_in_daily(text: str) -> list[str]:
    return re.findall(r"\[\[Events/[^\]]+\]\]", _section(text, "## 今天的事件卡"))


def _replace_section_body(text: str, heading: str, body_lines: list[str]) -> str:
    replacement = heading + "\n" + "\n".join(body_lines).rstrip() + "\n"
    return re.sub(rf"(?ms)^{re.escape(heading)}\n.*?(?=^## |\Z)", replacement, text)


def test_incremental_appends_new_events(tmp_path: Path, monkeypatch):
    keypulse_home = tmp_path / "kp-home"
    monkeypatch.setenv("KEYPULSE_HOME", str(keypulse_home))
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    cursor_path = keypulse_home / "sync-cursor.json"

    _insert_event(db_path, "09:00:00", "baseline one")
    _insert_event(db_path, "09:10:00", "baseline two")
    export_obsidian(vault_path, date_str=DATE, db_path=db_path)
    before_daily = _read(_daily_path(vault_path))
    before_links = set(_event_links_in_daily(before_daily))

    _insert_event(db_path, "10:00:00", "new event one")
    _insert_event(db_path, "10:10:00", "new event two")
    export_obsidian_incremental(db_path, vault_path, cursor_path, DATE)

    after_daily = _read(_daily_path(vault_path))
    after_links = set(_event_links_in_daily(after_daily))
    assert len(after_links - before_links) == 2


def test_incremental_dedupes(tmp_path: Path, monkeypatch):
    keypulse_home = tmp_path / "kp-home"
    monkeypatch.setenv("KEYPULSE_HOME", str(keypulse_home))
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    cursor_path = keypulse_home / "sync-cursor.json"

    _insert_event(db_path, "09:00:00", "baseline one")
    _insert_event(db_path, "09:10:00", "baseline two")
    export_obsidian(vault_path, date_str=DATE, db_path=db_path)
    _insert_event(db_path, "10:00:00", "new event one")

    export_obsidian_incremental(db_path, vault_path, cursor_path, DATE)
    after_first = _read(_daily_path(vault_path))
    export_obsidian_incremental(db_path, vault_path, cursor_path, DATE)
    after_second = _read(_daily_path(vault_path))

    assert after_second == after_first


def test_incremental_preserves_narrative(tmp_path: Path, monkeypatch):
    keypulse_home = tmp_path / "kp-home"
    monkeypatch.setenv("KEYPULSE_HOME", str(keypulse_home))
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    cursor_path = keypulse_home / "sync-cursor.json"

    _insert_event(db_path, "09:00:00", "baseline one")
    _insert_event(db_path, "09:10:00", "baseline two")
    export_obsidian(vault_path, date_str=DATE, db_path=db_path)

    daily_path = _daily_path(vault_path)
    customized = _replace_section_body(
        _read(daily_path),
        "## 今日主线",
        ["", "今天我把关键链路打通了，剩下的是收尾。", ""],
    )
    _write(daily_path, customized)

    _insert_event(db_path, "10:00:00", "new event one")
    export_obsidian_incremental(db_path, vault_path, cursor_path, DATE)
    after = _read(daily_path)

    assert "今天我把关键链路打通了，剩下的是收尾。" in _section(after, "## 今日主线")


def test_incremental_preserves_tomorrow_plan(tmp_path: Path, monkeypatch):
    keypulse_home = tmp_path / "kp-home"
    monkeypatch.setenv("KEYPULSE_HOME", str(keypulse_home))
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    cursor_path = keypulse_home / "sync-cursor.json"

    _insert_event(db_path, "09:00:00", "baseline one")
    export_obsidian(vault_path, date_str=DATE, db_path=db_path)

    daily_path = _daily_path(vault_path)
    text = _read(daily_path)
    text = re.sub(r"^> 明天我想：.*$", "> 明天我想：明早先做增量验收", text, flags=re.MULTILINE)
    _write(daily_path, text)

    _insert_event(db_path, "10:00:00", "new event one")
    export_obsidian_incremental(db_path, vault_path, cursor_path, DATE)

    assert "> 明天我想：明早先做增量验收" in _read(daily_path)


def test_cursor_updates(tmp_path: Path, monkeypatch):
    keypulse_home = tmp_path / "kp-home"
    monkeypatch.setenv("KEYPULSE_HOME", str(keypulse_home))
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    cursor_path = keypulse_home / "sync-cursor.json"

    _insert_event(db_path, "09:00:00", "baseline one")
    second_id = _insert_event(db_path, "09:10:00", "baseline two")
    export_obsidian(vault_path, date_str=DATE, db_path=db_path)
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(
        json.dumps({"last_event_id": second_id, "last_run_at": None}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    _insert_event(db_path, "10:00:00", "new event one")
    newest_id = _insert_event(db_path, "10:10:00", "new event two")
    export_obsidian_incremental(db_path, vault_path, cursor_path, DATE)

    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert cursor_payload["last_event_id"] == newest_id
    assert cursor_payload["last_run_at"]


def test_cursor_first_run(tmp_path: Path, monkeypatch):
    keypulse_home = tmp_path / "kp-home"
    monkeypatch.setenv("KEYPULSE_HOME", str(keypulse_home))
    db_path = tmp_path / "keypulse.db"
    vault_path = tmp_path / "vault"
    cursor_path = keypulse_home / "sync-cursor.json"

    newest_id = _insert_event(db_path, "09:00:00", "baseline one")
    export_obsidian_incremental(db_path, vault_path, cursor_path, DATE)

    assert cursor_path.exists()
    cursor_payload = json.loads(cursor_path.read_text(encoding="utf-8"))
    assert cursor_payload["last_event_id"] == newest_id
    assert "last_run_at" in cursor_payload
