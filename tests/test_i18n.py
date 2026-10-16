from __future__ import annotations

import penpal.i18n as i18n


def _reset_lang_cache(monkeypatch):
    monkeypatch.setattr(i18n, "_LANG_CACHE", None)


def test_detect_lang_prefers_keypulse_lang_env(monkeypatch):
    monkeypatch.setenv("PENPAL_LANG", "zh")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    _reset_lang_cache(monkeypatch)

    assert i18n.detect_lang() == "zh"
    assert i18n.current_lang() == "zh"
    assert i18n.T("中文", "English") == "中文"


def test_detect_lang_uses_lang_env_when_keypulse_lang_missing(monkeypatch):
    monkeypatch.delenv("PENPAL_LANG", raising=False)
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    _reset_lang_cache(monkeypatch)

    assert i18n.detect_lang() == "zh"


def test_detect_lang_defaults_to_en(monkeypatch):
    monkeypatch.delenv("PENPAL_LANG", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.setattr(i18n._locale, "getlocale", lambda: (None, None))
    _reset_lang_cache(monkeypatch)

    assert i18n.detect_lang() == "en"
    assert i18n.T("中文", "English") == "English"


def test_detect_lang_reads_legacy_keypulse_env_with_notice(monkeypatch, capsys):
    monkeypatch.delenv("PENPAL_LANG", raising=False)
    monkeypatch.setenv("KEYPULSE_LANG", "zh")
    _reset_lang_cache(monkeypatch)

    assert i18n.detect_lang() == "zh"
    assert "Deprecated environment variable KEYPULSE_LANG; use PENPAL_LANG" in capsys.readouterr().err
