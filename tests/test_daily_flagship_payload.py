from __future__ import annotations

from pathlib import Path

from keypulse.pipeline.daily_strategy import _build_recent_topic_history, _load_yesterday_anchor


def test_daily_flagship_context_helpers_read_real_may_9_history():
    history = _build_recent_topic_history("2026-05-09", days=7)
    yesterday_anchor = _load_yesterday_anchor("2026-05-09")

    assert isinstance(yesterday_anchor, str)
    assert history
    assert any(len(item["active_dates"]) >= 2 for item in history)
    assert all("anchor" in item for item in history)


def test_daily_flagship_context_helpers_skip_missing_vault(monkeypatch, tmp_path):
    missing_vault = tmp_path / "missing-vault"
    config = type(
        "Config",
        (),
        {"obsidian": type("Obsidian", (), {"vault_path": str(missing_vault)})()},
    )()

    monkeypatch.setattr("keypulse.config.Config.load", lambda: config)

    assert not Path(missing_vault).exists()
    assert _load_yesterday_anchor("2026-05-09") == ""
    assert _build_recent_topic_history("2026-05-09", days=7) == []
