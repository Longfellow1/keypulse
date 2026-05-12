from __future__ import annotations

import json
from pathlib import Path

from keypulse.config import Config


def _reset_transient_error_state(cfg: Config) -> None:
    """清掉重启前的临时错误状态，让 daemon 起来后 capability 从干净状态开始探测。"""
    from keypulse.store.repository import set_state

    set_state("llm_error_code", "")
    set_state("capture_error_code", "")

    state_path_str = str(cfg.model.state_path or "").strip()
    if not state_path_str:
        return
    state_path = Path(state_path_str).expanduser()
    if not state_path.exists():
        return
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    if "short_circuits" in payload:
        payload["short_circuits"] = {}
        state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
