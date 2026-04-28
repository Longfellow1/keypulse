from __future__ import annotations

from datetime import datetime, timezone

from keypulse.sources import registry
from keypulse.sources.types import DataSource, DataSourceInstance, SemanticEvent


class _FakeSource(DataSource):
    name = "fake"
    privacy_tier = "green"
    liveness = "always"

    def discover(self) -> list[DataSourceInstance]:
        return [
            DataSourceInstance(plugin=self.name, locator="/tmp/a", label="a"),
            DataSourceInstance(plugin=self.name, locator="/tmp/b", label="b"),
        ]

    def read(self, instance: DataSourceInstance, since: datetime, until: datetime):
        if instance.locator.endswith("a"):
            yield SemanticEvent(
                time=datetime(2026, 4, 28, 2, 0, tzinfo=timezone.utc),
                source=self.name,
                actor="alice",
                intent="A",
                artifact="commit:aaa",
                raw_ref="git:a:aaa",
                privacy_tier="green",
                metadata={"repo_path": instance.locator},
            )
        else:
            yield SemanticEvent(
                time=datetime(2026, 4, 28, 1, 0, tzinfo=timezone.utc),
                source=self.name,
                actor="bob",
                intent="B",
                artifact="commit:bbb",
                raw_ref="git:b:bbb",
                privacy_tier="green",
                metadata={"repo_path": instance.locator},
            )


class _BrokenSource(DataSource):
    name = "broken"
    privacy_tier = "green"
    liveness = "always"

    def discover(self) -> list[DataSourceInstance]:
        raise RuntimeError("boom")

    def read(self, instance: DataSourceInstance, since: datetime, until: datetime):
        raise RuntimeError("boom")


def test_discover_all_handles_plugin_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        registry,
        "_PLUGINS",
        {
            "fake": _FakeSource(),
            "broken": _BrokenSource(),
        },
    )

    discovered = registry.discover_all()

    assert set(discovered.keys()) == {"fake", "broken"}
    assert len(discovered["fake"]) == 2
    assert discovered["broken"] == []


def test_read_all_aggregates_and_sorts(monkeypatch) -> None:
    monkeypatch.setattr(
        registry,
        "_PLUGINS",
        {
            "fake": _FakeSource(),
            "broken": _BrokenSource(),
        },
    )

    events = list(
        registry.read_all(
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )

    assert [event.intent for event in events] == ["B", "A"]


def test_read_all_filters_by_source(monkeypatch) -> None:
    monkeypatch.setattr(
        registry,
        "_PLUGINS",
        {
            "fake": _FakeSource(),
        },
    )

    events = list(
        registry.read_all(
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
            source="fake",
        )
    )
    none_events = list(
        registry.read_all(
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
            source="missing",
        )
    )

    assert len(events) == 2
    assert none_events == []
