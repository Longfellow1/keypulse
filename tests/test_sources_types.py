from __future__ import annotations

from datetime import datetime, timezone

import pytest

from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent


class _DummySource(DataSource):
    name = "dummy"
    privacy_tier = "green"
    liveness = "always"

    def discover(self) -> list[DataSourceInstance]:
        return [
            DataSourceInstance(
                plugin=self.name,
                locator="/tmp/dummy",
                label="dummy",
            )
        ]

    def read(self, instance: DataSourceInstance, since: datetime, until: datetime):
        yield SemanticEvent(
            time=since,
            source=self.name,
            actor="tester",
            intent="test",
            artifact="artifact",
            raw_ref="raw",
            privacy_tier=self.privacy_tier,
            metadata={"locator": instance.locator},
        )


def test_semantic_event_normalizes_to_utc() -> None:
    event = SemanticEvent(
        time=datetime.fromisoformat("2026-04-28T10:20:00+08:00"),
        source="git_log",
        actor="Harland",
        intent="Add source registry",
        artifact="commit:abcdef1",
        raw_ref="git:keypulse:abcdef123456",
        privacy_tier="green",
        metadata={"repo_path": "/tmp/repo"},
    )

    assert event.time.tzinfo is not None
    assert event.time.utcoffset() == timezone.utc.utcoffset(event.time)
    assert event.time.isoformat() == "2026-04-28T02:20:00+00:00"


def test_semantic_event_rejects_naive_time() -> None:
    with pytest.raises(ValueError):
        SemanticEvent(
            time=datetime(2026, 4, 28, 10, 20, 0),
            source="git_log",
            actor="Harland",
            intent="Add source registry",
            artifact="commit:abcdef1",
            raw_ref="git:keypulse:abcdef123456",
            privacy_tier="green",
            metadata={},
        )


def test_data_source_instance_defaults_metadata() -> None:
    instance = DataSourceInstance(plugin="git_log", locator="/tmp/repo", label="repo (main)")

    assert instance.metadata == {}


def test_data_source_contract_shape() -> None:
    source = _DummySource()
    instances = source.discover()

    assert len(instances) == 1
    events = list(source.read(instances[0], datetime.now(timezone.utc), datetime.now(timezone.utc)))
    assert len(events) == 1
    assert events[0].source == "dummy"
