from keypulse.quality.base import Strategy


class StrategyRegistry:
    """登记所有策略，按注册顺序保存；支持禁用/启用。"""

    def __init__(self) -> None:
        self._strategies: list[Strategy] = []
        self._by_id: dict[str, Strategy] = {}
        self._disabled: set[str] = set()

    def register(self, strategy: Strategy) -> None:
        """注册策略。id 冲突时 raise ValueError。"""
        if strategy.id in self._by_id:
            raise ValueError(f"strategy id already registered: {strategy.id}")
        self._strategies.append(strategy)
        self._by_id[strategy.id] = strategy

    def disable(self, strategy_id: str) -> None:
        """禁用；幂等；不存在的 id 也不抛错。"""
        if strategy_id in self._by_id:
            self._disabled.add(strategy_id)

    def enable(self, strategy_id: str) -> None:
        """启用；幂等。"""
        self._disabled.discard(strategy_id)

    def is_disabled(self, strategy_id: str) -> bool:
        return strategy_id in self._disabled

    def for_layer(self, layer: str) -> list[Strategy]:
        """返回该层**已启用**的策略，保持注册顺序。"""
        return [
            strategy
            for strategy in self._strategies
            if strategy.layer == layer and strategy.id not in self._disabled
        ]

    def all(self) -> list[Strategy]:
        """返回所有策略（含已禁用），供 report 用。"""
        return list(self._strategies)
