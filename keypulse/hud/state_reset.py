from __future__ import annotations

from keypulse.config import Config


def _reset_transient_error_state(cfg: Config) -> None:
    """清掉重启前的临时错误状态，让 daemon 起来后 capability 从干净状态开始探测。"""
    from keypulse.store.repository import set_state

    set_state("capture_error_code", "")
