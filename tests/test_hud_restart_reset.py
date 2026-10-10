from __future__ import annotations

from keypulse.config import Config
from keypulse.hud.state_reset import _reset_transient_error_state
from keypulse.store.db import close, init_db
from keypulse.store.repository import get_state, set_state


def test_reset_clears_capture_error_code(tmp_path) -> None:
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    try:
        set_state("capture_error_code", "capture_failed")

        _reset_transient_error_state(Config())

        assert get_state("capture_error_code") == ""
    finally:
        close()
