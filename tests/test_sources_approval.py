from __future__ import annotations

import hashlib
import json
from pathlib import Path

from click.testing import CliRunner

from keypulse.sources import discover as discover_module
from keypulse.sources.approval import ApprovalStore
from keypulse.sources.discover import sources_group
from keypulse.sources.discoverers import CandidateSource


def _candidate(path: str, *, app_hint: str = "App", confidence: str = "high") -> CandidateSource:
    return CandidateSource(
        discoverer="sqlite",
        path=path,
        app_hint=app_hint,
        schema_signature="messages,events",
        confidence=confidence,
    )


def _patch_candidates(monkeypatch, candidates: list[CandidateSource]) -> None:
    monkeypatch.setattr(discover_module, "discover_all", lambda: {})
    monkeypatch.setattr(
        discover_module,
        "discover_all_candidates",
        lambda *, exclude_paths: {"sqlite": candidates},
    )


def test_candidate_id_is_deterministic(tmp_path: Path) -> None:
    store = ApprovalStore(path=tmp_path / "approval.json")
    candidate = _candidate("/tmp/alpha.db", app_hint="Cursor")

    candidate_id = store.candidate_id(candidate)

    expected = hashlib.sha1("sqlite:/tmp/alpha.db".encode("utf-8")).hexdigest()[:12]
    assert candidate_id == expected
    assert candidate_id == store.candidate_id(candidate)


def test_store_approve_reject_unset_persists(tmp_path: Path) -> None:
    store_path = tmp_path / "nested" / "sources-approval.json"
    store = ApprovalStore(path=store_path)
    candidate = _candidate("/tmp/persist.db", app_hint="Notion")

    approved = store.approve(candidate, note="keep")

    assert approved.status == "approved"
    assert approved.note == "keep"
    assert store.status(approved.candidate_id) == "approved"
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["approved"][approved.candidate_id]["discoverer"] == "sqlite"
    assert payload["approved"][approved.candidate_id]["path"] == "/tmp/persist.db"
    assert payload["approved"][approved.candidate_id]["app_hint"] == "Notion"
    assert payload["approved"][approved.candidate_id]["user_note"] == "keep"

    rejected = store.reject(candidate, reason="user_choice")

    assert rejected.status == "rejected"
    assert rejected.note == "user_choice"
    assert store.status(rejected.candidate_id) == "rejected"
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert rejected.candidate_id not in payload["approved"]
    assert payload["rejected"][rejected.candidate_id]["reason"] == "user_choice"

    store.unset(rejected.candidate_id)

    assert store.status(rejected.candidate_id) == "unknown"
    assert store.list_approved() == []
    assert store.list_rejected() == []


def test_store_handles_missing_or_invalid_json_as_empty(tmp_path: Path) -> None:
    store_path = tmp_path / "sources-approval.json"
    store_path.write_text("{bad-json", encoding="utf-8")

    store = ApprovalStore(path=store_path)

    assert store.status("deadbeefcafe") == "unknown"
    assert store.list_approved() == []
    assert store.list_rejected() == []


def test_candidates_command_shows_status_prefix_and_id(monkeypatch, tmp_path: Path) -> None:
    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    approved = _candidate("/tmp/approved.db", app_hint="Cursor", confidence="high")
    rejected = _candidate("/tmp/rejected.db", app_hint="Chrome", confidence="medium")
    unknown = _candidate("/tmp/unknown.db", app_hint="Warp", confidence="medium")

    store.approve(approved)
    store.reject(rejected)

    monkeypatch.setattr(discover_module, "ApprovalStore", lambda: store)
    _patch_candidates(monkeypatch, [approved, rejected, unknown])

    result = CliRunner().invoke(sources_group, ["candidates"])

    assert result.exit_code == 0
    assert "[✅]" in result.output
    assert "[❌]" in result.output
    assert "[新]" in result.output
    assert f"id={store.candidate_id(approved)}" in result.output
    assert f"id={store.candidate_id(rejected)}" in result.output
    assert f"id={store.candidate_id(unknown)}" in result.output


def test_approve_reject_unset_and_list_commands(monkeypatch, tmp_path: Path) -> None:
    store = ApprovalStore(path=tmp_path / "sources-approval.json")
    candidate = _candidate("/tmp/flow.db", app_hint="Cursor")
    candidate_id = store.candidate_id(candidate)

    monkeypatch.setattr(discover_module, "ApprovalStore", lambda: store)
    _patch_candidates(monkeypatch, [candidate])

    bad = CliRunner().invoke(sources_group, ["approve", "abc123ef0987"])
    assert bad.exit_code != 0
    assert "candidate id not found" in bad.output.lower()

    store.reject(candidate, reason="user_choice")
    approved = CliRunner().invoke(sources_group, ["approve", candidate_id, "--note", "test"])
    assert approved.exit_code == 0
    assert "warning" in approved.output.lower()
    assert store.status(candidate_id) == "approved"

    rejected = CliRunner().invoke(sources_group, ["reject", candidate_id, "--reason", "noise"])
    assert rejected.exit_code == 0
    assert store.status(candidate_id) == "rejected"

    listed_rejected = CliRunner().invoke(sources_group, ["list-rejected"])
    assert listed_rejected.exit_code == 0
    assert candidate_id in listed_rejected.output
    assert "noise" in listed_rejected.output

    listed_approved = CliRunner().invoke(sources_group, ["list-approved"])
    assert listed_approved.exit_code == 0

    unset = CliRunner().invoke(sources_group, ["unset", candidate_id])
    assert unset.exit_code == 0
    assert store.status(candidate_id) == "unknown"
