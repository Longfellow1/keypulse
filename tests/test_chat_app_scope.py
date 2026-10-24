"""Issue #26 (AT18, AT20): chat apps are excluded from content capture by default; an explicit per-app
opt-in stores them as Private, and only a host granted Private can search them through search_context.
Synthetic data only; HOME is isolated so no real config is read."""
from __future__ import annotations

import pytest

from penpal.foundation.chat_apps import chat_app_key, normalize_opt_ins
from penpal.capture.manager import CaptureManager
from penpal.capture.normalizer import (
    normalize_ax_text_event,
    normalize_clipboard_event,
    normalize_keyboard_chunk_event,
    normalize_window_event,
)
from penpal.config import Config
from penpal.mcp import auth
from penpal.store import buckets
from penpal.store.db import close, get_conn, init_db
from mcp_helpers import search

WECHAT = dict(app_name="WeChat", process_name="com.tencent.xinWeChat")
SLACK = dict(app_name="Slack", process_name="com.tinyspeck.slackmacgap")
ONEPASSWORD = dict(app_name="1Password 7", process_name="com.agilebits.onepassword7")
NOTES = dict(app_name="Notes", process_name="com.apple.Notes")
TS = "2026-03-02T10:00:{:02d}+00:00"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def db(tmp_path, monkeypatch):
    buckets.reset_cache()
    init_db(tmp_path / "kp.db")
    yield tmp_path
    close()
    buckets.reset_cache()


def _use_config(monkeypatch, cfg: Config) -> None:
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))
    buckets.reset_cache()


def _stored_apps() -> list[str]:
    return [r[0] for r in get_conn().execute("SELECT DISTINCT app_name FROM raw_events WHERE app_name IS NOT NULL")]


def _content_rows() -> list[dict]:
    return [dict(r) for r in get_conn().execute(
        "SELECT source, app_name, bucket, content_text, window_title FROM raw_events WHERE source != 'idle'")]


# ── chat app list and config ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("app,process,key", [
    ("WeChat", "com.tencent.xinWeChat", "WeChat"),
    ("微信", None, "WeChat"),
    (None, "com.tencent.qq", "QQ"),
    ("Telegram", "ru.keepcoder.Telegram", "Telegram"),
    ("Slack", None, "Slack"),
    ("Discord", "com.hnc.Discord", "Discord"),
    ("Messages", "com.apple.MobileSMS", "Messages"),
    ("WhatsApp", "com.whatsapp.messenger", "WhatsApp"),
    ("Lark", "com.electron.lark", "Feishu"),
    ("DingTalk", "com.alibaba.DingTalkMac", "DingTalk"),
])
def test_chat_apps_are_recognised_by_name_or_bundle_id(app, process, key):
    assert chat_app_key(app, process) == key


def test_non_chat_apps_are_not_chat_apps():
    assert chat_app_key("Notes", "com.apple.Notes") is None
    assert chat_app_key("1Password 7", "com.agilebits.onepassword7") is None
    assert chat_app_key("Visual Studio Code", None) is None
    assert chat_app_key(None, None) is None


def test_opt_in_accepts_keys_and_names_and_rejects_unknown():
    assert normalize_opt_ins(["wechat", "Lark", "WeChat"]) == ["WeChat", "Feishu"]
    with pytest.raises(ValueError):
        normalize_opt_ins(["Notes"])
    with pytest.raises(ValueError):
        Config.model_validate({"privacy": {"chat_apps_opt_in": ["Notepad"]}})


def test_no_opt_in_by_default():
    assert Config().privacy.chat_apps_opt_in == []


# ── AT18: excluded app produces no content events; normal app does ──────────────────────────
def test_excluded_chat_app_produces_no_content_events(db):
    mgr = CaptureManager(Config())
    mgr._process_event(normalize_window_event("window_focus_session", WECHAT["app_name"], "Alice - WeChat chat",
                                             WECHAT["process_name"], ts_start=TS.format(1), ts_end=TS.format(20)))
    mgr._process_event(normalize_keyboard_chunk_event("zebracorn dinner plan for friday", ts_start=TS.format(2), **WECHAT))
    mgr._process_event(normalize_ax_text_event("zebracorn meeting moved to noon", window_title="Alice", ts_start=TS.format(3), **WECHAT))
    mgr._process_event(normalize_clipboard_event("zebracorn invoice copied from chat", app_name="WeChat", ts_start=TS.format(4)))
    for ev in (SLACK, ONEPASSWORD):
        mgr._process_event(normalize_keyboard_chunk_event("zebracorn secret from a chat app", ts_start=TS.format(5), **ev))
        mgr._process_event(normalize_clipboard_event("zebracorn copied text", ts_start=TS.format(6), app_name=ev["app_name"]))
    assert _content_rows() == []
    assert not any("zebracorn" in str(v) for v in mgr.health()["gate_hits"])


def test_normal_app_window_and_clipboard_are_findable_via_search_context(db, monkeypatch):
    _use_config(monkeypatch, Config())
    mgr = CaptureManager(Config())
    mgr._process_event(normalize_window_event("window_focus_session", NOTES["app_name"], "Zebracorn roadmap draft",
                                             NOTES["process_name"], ts_start=TS.format(1), ts_end=TS.format(20)))
    mgr._process_event(normalize_clipboard_event("zebracorn invoice number 4471", app_name="Notes", ts_start=TS.format(2)))
    token = auth.create_grant("work-host", buckets=["Work", "Personal"])
    sources = {r["source"] for r in search(token, ["zebracorn"])["results"]}
    assert {"window", "clipboard"} <= sources


# ── AT20: opt-in chat app lands in Private and is scoped by grant ───────────────────────────
def _seed_wechat(mgr: CaptureManager) -> None:
    mgr._process_event(normalize_keyboard_chunk_event("zebracorn dinner plan for friday", ts_start=TS.format(2), **WECHAT))
    mgr._process_event(normalize_clipboard_event("zebracorn invoice copied from chat", app_name="WeChat", ts_start=TS.format(4)))


def test_chat_app_not_opted_in_has_no_content_and_stays_out_of_search(db, monkeypatch):
    _use_config(monkeypatch, Config())
    _seed_wechat(CaptureManager(Config()))
    token = auth.create_grant("private-host", buckets=["Private"])
    assert search(token, ["zebracorn"])["status"] == "no_results"


def test_opted_in_chat_app_is_private_and_hidden_from_work_personal_hosts(db, monkeypatch):
    cfg = Config.model_validate({"privacy": {"chat_apps_opt_in": ["WeChat"]}})
    _use_config(monkeypatch, cfg)
    mgr = CaptureManager(cfg)
    _seed_wechat(mgr)
    # Opt-in applies only to the listed app: Slack stays excluded, a password manager stays excluded.
    mgr._process_event(normalize_keyboard_chunk_event("zebracorn slack message text", ts_start=TS.format(5), **SLACK))
    mgr._process_event(normalize_keyboard_chunk_event("zebracorn vault typed text", ts_start=TS.format(6), **ONEPASSWORD))

    rows = _content_rows()
    assert {r["app_name"] for r in rows} == {"WeChat"}
    assert {r["bucket"] for r in rows} == {"Private"}

    work_personal = auth.create_grant("work-personal-host", buckets=["Work", "Personal"])
    assert search(work_personal, ["zebracorn"])["status"] == "no_results"

    private = auth.create_grant("private-host", buckets=["Private"])
    found = search(private, ["zebracorn"])["results"]
    assert {r["bucket"] for r in found} == {"Private"}
    assert {"keyboard_chunk", "clipboard"} <= {r["source"] for r in found}


def test_opt_in_loses_to_explicit_config_bucket_override(db, monkeypatch):
    cfg = Config.model_validate({
        "privacy": {"chat_apps_opt_in": ["WeChat"]},
        "buckets": {"app_defaults": {"WeChat": "Work"}},
    })
    _use_config(monkeypatch, cfg)
    assert buckets.classify_capture_app("WeChat", "com.tencent.xinWeChat") == "Work"
    assert buckets.classify_capture_app("Slack", None) == "Work"  # preset, unchanged without opt-in
