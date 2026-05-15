from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from keypulse.sources.approval import ApprovalStore
from keypulse.sources.discoverers import CandidateSource
from keypulse.sources.plugins.markdown_vault import MarkdownVaultSource
from keypulse.sources.types import DataSourceInstance


def _approve_markdown(store: ApprovalStore, path: Path) -> str:
    candidate = CandidateSource(
        discoverer="markdown_vault",
        path=str(path.resolve()),
        app_hint="Obsidian",
        schema_signature="markdown:1",
        shape="document_file",
        confidence="high",
    )
    return store.approve(candidate, note="test").candidate_id


def test_markdown_vault_discover_and_read(tmp_path: Path) -> None:
    vault = tmp_path / "Knowledge"
    (vault / ".obsidian").mkdir(parents=True, exist_ok=True)

    note = vault / "Daily" / "2026-04-28.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntags: [daily, keypulse]\n---\n# Sprint 1.5 progress\nBody should not be read\n",
        encoding="utf-8",
    )

    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    _approve_markdown(store, vault)

    source = MarkdownVaultSource(approval_store=store)
    instances = source.discover()

    assert len(instances) == 1
    instance = instances[0]
    assert instance.label == "Knowledge"
    assert instance.metadata["vault_name"] == "Knowledge"
    assert instance.metadata["note_count"] == 1

    now = datetime.now(timezone.utc)
    events = list(source.read(instance, now - timedelta(days=10), now + timedelta(days=1)))

    assert len(events) == 1
    event = events[0]
    assert event.source == "markdown_vault"
    assert event.actor == "user"
    assert event.intent == "Sprint 1.5 progress"
    assert event.artifact == "Daily/2026-04-28.md"
    assert event.raw_ref == "markdown_vault:Knowledge:Daily/2026-04-28.md"
    assert event.privacy_tier == "yellow"
    assert event.metadata["shape"] == "document_file"


def test_markdown_vault_read_missing_returns_empty() -> None:
    source = MarkdownVaultSource(roots=[])
    instance = DataSourceInstance(plugin="markdown_vault", locator="/tmp/missing", label="x", metadata={"vault_name": "x"})
    events = list(
        source.read(
            instance,
            datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 59, tzinfo=timezone.utc),
        )
    )
    assert events == []


def test_markdown_vault_read_filters_by_time(tmp_path: Path) -> None:
    vault = tmp_path / "Vault"
    (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
    note = vault / "task.md"
    note.write_text("# recent note\n", encoding="utf-8")

    source = MarkdownVaultSource(roots=[vault])
    instance = source.discover()[0]
    past = datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)
    before = datetime(2020, 1, 2, 0, 0, tzinfo=timezone.utc)

    events = list(source.read(instance, past, before))
    assert events == []


def test_markdown_vault_uses_filename_when_h1_missing(tmp_path: Path) -> None:
    vault = tmp_path / "Notes"
    note = vault / "misc.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("plain text body\n", encoding="utf-8")

    source = MarkdownVaultSource(roots=[vault])
    instance = source.discover()[0]
    now = datetime.now(timezone.utc)

    events = list(source.read(instance, now - timedelta(days=1), now + timedelta(days=1)))
    assert len(events) == 1
    assert events[0].intent == "misc"
    assert events[0].artifact == "misc.txt"
