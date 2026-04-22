from __future__ import annotations

import json
from pathlib import Path

import pytest

from keypulse.quality import StrategyRegistry, StrategyRunner
from keypulse.quality.strategies import register_cluster_strategies

GOLDEN_PATH = Path(__file__).parent / "golden" / "cluster_topics.jsonl"


def _load_samples():
    samples = []
    for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        samples.append(json.loads(line))
    return samples


@pytest.fixture
def cluster_runner():
    reg = StrategyRegistry()
    register_cluster_strategies(reg)
    return StrategyRunner(reg, log_path=None)


@pytest.mark.parametrize("sample", _load_samples(), ids=lambda s: s.get("note") or s.get("value"))
def test_golden_cluster_sample(sample, cluster_runner):
    verdict = cluster_runner.check(sample["value"], layer=sample["layer"])
    assert verdict.accept == sample["expected_accept"], (
        f"golden sample mismatch: value={sample['value']!r} note={sample.get('note')!r} "
        f"expected_accept={sample['expected_accept']} got={verdict.accept} reason={verdict.reason!r}"
    )
    if not sample["expected_accept"] and sample.get("expected_reason_contains"):
        assert sample["expected_reason_contains"].lower() in verdict.reason.lower(), (
            f"reason mismatch: value={sample['value']!r} "
            f"expected_contains={sample['expected_reason_contains']!r} got={verdict.reason!r}"
        )
