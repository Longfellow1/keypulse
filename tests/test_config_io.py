from __future__ import annotations

from penpal.config import read_cleaning_config, read_config, write_default_config


def test_read_config_uses_explicit_path(tmp_path):
    path = tmp_path / "custom.toml"
    path.write_text("[app]\nflush_interval_sec = 9\n", encoding="utf-8")

    assert read_config(path).app.flush_interval_sec == 9


def test_write_default_config_creates_and_validates_then_respects_force(tmp_path):
    path = tmp_path / "nested" / "config.toml"

    assert write_default_config(path) == "created"
    assert read_config(path).browser_url.emit_on_url_change_only is True
    assert write_default_config(path) == "exists"

    path.write_text("[app]\nflush_interval_sec = 17\n", encoding="utf-8")
    assert write_default_config(path, force=True) == "updated"
    assert read_config(path).app.flush_interval_sec == 5


def test_read_cleaning_config_reads_explicit_path_and_defaults_when_missing(tmp_path):
    path = tmp_path / "cleaning.toml"
    assert read_cleaning_config(path) == {}

    path.write_text('privacy_max_tier = "green"\n', encoding="utf-8")
    assert read_cleaning_config(path) == {"privacy_max_tier": "green"}
