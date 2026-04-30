from __future__ import annotations

from datetime import datetime, timezone

from keypulse.sources import registry
from keypulse.sources.cleaning.config import CleaningConfig
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


class _MixedTierSource(DataSource):
    """Emits one event per privacy_tier so we can assert the filter."""

    name = "mixed"
    privacy_tier = "yellow"
    liveness = "always"

    def discover(self) -> list[DataSourceInstance]:
        return [DataSourceInstance(plugin=self.name, locator="/tmp/m", label="m")]

    def read(self, instance: DataSourceInstance, since: datetime, until: datetime):
        base = datetime(2026, 4, 28, 1, 0, tzinfo=timezone.utc)
        for tier_idx, tier in enumerate(("green", "yellow", "red", "unknown")):
            yield SemanticEvent(
                time=base.replace(minute=tier_idx),
                source=self.name,
                actor=f"actor_{tier}",
                intent=f"intent_{tier}",
                artifact=f"art_{tier}",
                raw_ref=f"ref_{tier}",
                privacy_tier=tier,
                metadata={"tier": tier},
            )


def _patch_cleaning_config(monkeypatch, *, privacy_max_tier: str) -> None:
    monkeypatch.setattr(
        registry,
        "load_cleaning_config",
        lambda: CleaningConfig(privacy_max_tier=privacy_max_tier),
    )


def test_privacy_max_tier_default_yellow_drops_red(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_PLUGINS", {"mixed": _MixedTierSource()})
    _patch_cleaning_config(monkeypatch, privacy_max_tier="yellow")

    events = list(
        registry.read_all(
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )
    tiers = [event.privacy_tier for event in events]

    assert "green" in tiers and "yellow" in tiers
    assert "red" not in tiers
    assert "unknown" not in tiers


def test_privacy_max_tier_green_drops_yellow_and_red(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_PLUGINS", {"mixed": _MixedTierSource()})
    _patch_cleaning_config(monkeypatch, privacy_max_tier="green")

    events = list(
        registry.read_all(
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )
    tiers = [event.privacy_tier for event in events]

    assert tiers == ["green"]


def test_privacy_max_tier_red_keeps_known_tiers_only(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_PLUGINS", {"mixed": _MixedTierSource()})
    _patch_cleaning_config(monkeypatch, privacy_max_tier="red")

    events = list(
        registry.read_all(
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )
    tiers = sorted(event.privacy_tier for event in events)

    # Unknown tier still drops (fail closed); green/yellow/red kept.
    assert tiers == ["green", "red", "yellow"]


def test_is_tier_allowed_unknowns_fail_closed() -> None:
    assert registry._is_tier_allowed("green", "yellow") is True
    assert registry._is_tier_allowed("yellow", "yellow") is True
    assert registry._is_tier_allowed("red", "yellow") is False
    assert registry._is_tier_allowed("", "yellow") is False
    assert registry._is_tier_allowed(None, "yellow") is False
    assert registry._is_tier_allowed("totally-bogus", "red") is False
    # Unknown max_tier defaults to yellow rank.
    assert registry._is_tier_allowed("yellow", "bogus") is True
    assert registry._is_tier_allowed("red", "bogus") is False
