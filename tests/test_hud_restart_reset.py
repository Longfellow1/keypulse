from __future__ import annotations

from penpal.config import Config
from penpal.hud.state_reset import _reset_transient_error_state
from penpal.store.db import close, init_db
from penpal.store.repository import get_state, set_state


def test_reset_clears_capture_error_code(tmp_path) -> None:
    init_db(tmp_path / ".penpal" / "penpal.db")
    try:
        set_state("capture_error_code", "capture_failed")

        _reset_transient_error_state(Config())

        assert get_state("capture_error_code") == ""
    finally:
        close()
