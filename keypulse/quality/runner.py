from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import json
import logging

from keypulse.quality.base import Verdict
from keypulse.quality.registry import StrategyRegistry

logger = logging.getLogger(__name__)


class StrategyRunner:
    """按 layer 跑策略链；第一条 reject 即终止并返回该 Verdict。
    命中写入 log_path（JSONL，append 模式）。
    """

    def __init__(self, registry: StrategyRegistry, log_path: Path | None = None) -> None:
        self._registry = registry
        self._log_path = log_path
        self._evals: defaultdict[str, int] = defaultdict(int)
        self._hits: defaultdict[str, int] = defaultdict(int)

    def check(self, value: str, layer: str, context: dict | None = None) -> Verdict:
        """按注册顺序跑该 layer 已启用的策略。
        第一条 accept=False 返回该 Verdict；全部通过返回 Verdict(accept=True)。
        每调用一条策略算一次 evaluation；拒绝时算一次 hit。
        每次拒绝写入一条 JSONL 日志。
        """
        for strategy in self._registry.for_layer(layer):
            self._evals[strategy.id] += 1
            verdict = strategy.apply(value, context)
            if not verdict.accept:
                self._hits[strategy.id] += 1
                self._write_hit(strategy.id, layer, verdict)
                return verdict
        return Verdict(accept=True)

    def report(self) -> dict[str, dict]:
        """返回 {strategy_id: {"hits": int, "evals": int, "description": str,
                                "layer": str, "disabled": bool}}"""
        report: dict[str, dict] = {}
        for strategy in self._registry.all():
            report[strategy.id] = {
                "hits": self._hits[strategy.id],
                "evals": self._evals[strategy.id],
                "description": strategy.description,
                "layer": strategy.layer,
                "disabled": self._registry.is_disabled(strategy.id),
            }
        return report

    def _write_hit(self, strategy_id: str, layer: str, verdict: Verdict) -> None:
        if self._log_path is None:
            return

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategy_id": strategy_id,
            "layer": layer,
            "accept": verdict.accept,
            "reason": verdict.reason,
        }

        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("failed to write quality strategy log to %s: %s", self._log_path, exc)
