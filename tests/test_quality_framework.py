from __future__ import annotations

import json

import pytest

from keypulse.quality import Strategy, StrategyRegistry, StrategyRunner, Verdict


class AcceptStrategy(Strategy):
    id = "TEST_ACCEPT"
    version = "0.1.0"
    layer = "cluster"
    description = "Always accepts"

    def apply(self, value, context=None):
        return Verdict(accept=True)


class RejectStrategy(Strategy):
    id = "TEST_REJECT"
    version = "0.1.0"
    layer = "cluster"
    description = "Always rejects"

    def apply(self, value, context=None):
        return Verdict(accept=False, reason="test")


def test_strategy_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        Strategy()


def test_concrete_strategy_instantiates_and_returns_verdict():
    strategy = AcceptStrategy()

    verdict = strategy.apply("value")

    assert verdict == Verdict(accept=True)


def test_registry_register_rejects_duplicate_strategy_ids():
    registry = StrategyRegistry()

    registry.register(AcceptStrategy())

    with pytest.raises(ValueError):
        registry.register(AcceptStrategy())


def test_registry_disable_enable_and_is_disabled_are_idempotent():
    registry = StrategyRegistry()
    strategy = AcceptStrategy()
    registry.register(strategy)

    assert registry.is_disabled(strategy.id) is False

    registry.disable(strategy.id)
    assert registry.is_disabled(strategy.id) is True

    registry.disable(strategy.id)
    assert registry.is_disabled(strategy.id) is True

    registry.enable(strategy.id)
    assert registry.is_disabled(strategy.id) is False

    registry.enable(strategy.id)
    assert registry.is_disabled(strategy.id) is False

    registry.disable("missing")
    assert registry.is_disabled("missing") is False


def test_registry_for_layer_filters_disabled_strategies_and_preserves_registration_order():
    registry = StrategyRegistry()

    class FirstStrategy(AcceptStrategy):
        id = "FIRST"
        description = "First"

    class SecondStrategy(AcceptStrategy):
        id = "SECOND"
        description = "Second"

    class OtherLayerStrategy(AcceptStrategy):
        id = "OTHER"
        layer = "filter"
        description = "Other layer"

    first = FirstStrategy()
    second = SecondStrategy()
    other = OtherLayerStrategy()

    registry.register(first)
    registry.register(second)
    registry.register(other)
    registry.disable(second.id)

    assert registry.for_layer("cluster") == [first]


def test_registry_all_includes_disabled_strategies():
    registry = StrategyRegistry()
    strategy = AcceptStrategy()

    registry.register(strategy)
    registry.disable(strategy.id)

    assert registry.all() == [strategy]


def test_runner_check_returns_accept_when_all_strategies_pass(tmp_path):
    registry = StrategyRegistry()
    registry.register(AcceptStrategy())
    runner = StrategyRunner(registry, log_path=tmp_path / "quality.jsonl")

    verdict = runner.check("value", "cluster")

    assert verdict == Verdict(accept=True)


def test_runner_check_stops_at_first_reject_and_does_not_call_later_strategies(tmp_path):
    registry = StrategyRegistry()
    calls: list[str] = []

    class FirstRejectStrategy(Strategy):
        id = "FIRST_REJECT"
        version = "0.1.0"
        layer = "cluster"
        description = "Rejects first"

        def apply(self, value, context=None):
            calls.append("reject")
            return Verdict(accept=False, reason="stop")

    class ShouldNotRunStrategy(Strategy):
        id = "SHOULD_NOT_RUN"
        version = "0.1.0"
        layer = "cluster"
        description = "Should not run"

        def apply(self, value, context=None):
            calls.append("late")
            return Verdict(accept=True)

    registry.register(FirstRejectStrategy())
    registry.register(ShouldNotRunStrategy())
    runner = StrategyRunner(registry, log_path=tmp_path / "quality.jsonl")

    verdict = runner.check("value", "cluster")

    assert verdict == Verdict(accept=False, reason="stop")
    assert calls == ["reject"]


def test_runner_check_skips_disabled_strategies(tmp_path):
    registry = StrategyRegistry()
    accepted = AcceptStrategy()
    rejected = RejectStrategy()
    registry.register(rejected)
    registry.register(accepted)
    registry.disable(rejected.id)
    runner = StrategyRunner(registry, log_path=tmp_path / "quality.jsonl")

    verdict = runner.check("value", "cluster")

    assert verdict == Verdict(accept=True)


def test_runner_report_includes_expected_fields(tmp_path):
    registry = StrategyRegistry()
    accept = AcceptStrategy()
    reject = RejectStrategy()
    registry.register(accept)
    registry.register(reject)
    registry.disable(reject.id)
    runner = StrategyRunner(registry, log_path=tmp_path / "quality.jsonl")

    runner.check("value", "cluster")

    report = runner.report()

    assert report[accept.id]["hits"] == 0
    assert report[accept.id]["evals"] == 1
    assert report[accept.id]["description"] == accept.description
    assert report[accept.id]["layer"] == accept.layer
    assert report[accept.id]["disabled"] is False

    assert report[reject.id]["hits"] == 0
    assert report[reject.id]["evals"] == 0
    assert report[reject.id]["description"] == reject.description
    assert report[reject.id]["layer"] == reject.layer
    assert report[reject.id]["disabled"] is True


def test_runner_writes_jsonl_log_for_rejections(tmp_path):
    log_path = tmp_path / "nested" / "quality.jsonl"
    registry = StrategyRegistry()
    registry.register(RejectStrategy())
    runner = StrategyRunner(registry, log_path=log_path)

    verdict = runner.check("value", "cluster", context={"source": "test"})

    assert verdict == Verdict(accept=False, reason="test")
    assert log_path.exists()

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["strategy_id"] == "TEST_REJECT"
    assert payload["layer"] == "cluster"
    assert payload["accept"] is False
    assert payload["reason"] == "test"
    assert "ts" in payload
