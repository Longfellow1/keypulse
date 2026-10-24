from __future__ import annotations

import tomllib
from pathlib import Path

from penpal.config import Config


def test_repo_config_loads_light_capture_defaults():
    config_path = Path(__file__).resolve().parents[1] / "config.toml"

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    config = Config.model_validate(data)

    assert config.watchers.ax_text is False
    assert config.ax_text.poll_interval_sec == 1.0
    assert config.privacy.camera_scene_pause is True
