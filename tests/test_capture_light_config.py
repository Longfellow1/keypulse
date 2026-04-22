from __future__ import annotations

import tomllib
from pathlib import Path

from keypulse.config import Config


def test_repo_config_loads_light_capture_defaults():
    config_path = Path(__file__).resolve().parents[1] / "config.toml"

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    config = Config.model_validate(data)

    assert config.watchers.ax_text is False
    assert config.watchers.keyboard_chunk is False
    assert config.watchers.ocr is False
    assert config.ocr.provider == "vision_native"
    assert config.ocr.window_switch_delay_sec == 0.8
    assert config.keyboard_chunk.silence_sec == 2.0
    assert config.keyboard_chunk.force_flush_sec == 2.0
    assert config.privacy.camera_scene_pause is True
