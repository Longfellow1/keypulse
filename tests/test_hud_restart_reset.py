from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from keypulse.store.db import close, init_db
from keypulse.store.repository import get_state, set_state
from keypulse.hud.state_reset import _reset_transient_error_state


def _cfg_with_state_path(state_path: Path) -> SimpleNamespace:
    return SimpleNamespace(model=SimpleNamespace(state_path=str(state_path)))


def test_reset_clears_llm_error_code(tmp_path) -> None:
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    try:
        set_state("llm_error_code", "llm_circuit_open")

        _reset_transient_error_state(_cfg_with_state_path(tmp_path / "missing-model-state.json"))

        assert get_state("llm_error_code") == ""
    finally:
        close()


def test_reset_clears_gateway_short_circuits(tmp_path) -> None:
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    try:
        state_path = tmp_path / "model-state.json"
        state_path.write_text(
            json.dumps(
                {
                    "short_circuits": {
                        "openai": {
                            "until": "2099-01-01T00:00:00+00:00",
                            "fail_count": 5,
                        },
                    },
                    "other_field": "keep_me",
                }
            ),
            encoding="utf-8",
        )

        _reset_transient_error_state(_cfg_with_state_path(state_path))

        payload = json.loads(state_path.read_text(encoding="utf-8"))
        assert payload["short_circuits"] == {}
        assert payload["other_field"] == "keep_me"
    finally:
        close()


def test_reset_handles_missing_state_file(tmp_path) -> None:
    init_db(tmp_path / ".keypulse" / "keypulse.db")
    try:
        set_state("llm_error_code", "llm_circuit_open")
        set_state("capture_error_code", "capture_failed")

        _reset_transient_error_state(_cfg_with_state_path(tmp_path / "missing-model-state.json"))

        assert get_state("llm_error_code") == ""
        assert get_state("capture_error_code") == ""
    finally:
        close()
