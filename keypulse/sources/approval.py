from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keypulse.sources.discoverers import CandidateSource


@dataclass
class ApprovalRecord:
    candidate_id: str
    status: str
    metadata: dict[str, str]
    timestamp: datetime | None
    note: str = ""


class ApprovalStore:
    def __init__(self, path: Path | None = None):
        if path is not None:
            self.path = path
            return
        override = os.environ.get("KEYPULSE_APPROVAL_PATH")
        if override:
            self.path = Path(override).expanduser()
            return
        self.path = Path.home() / ".keypulse" / "sources-approval.json"

    def candidate_id(self, candidate: CandidateSource) -> str:
        raw = f"{candidate.discoverer}:{candidate.path}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()[:12]

    def status(self, candidate_id: str) -> str:
        payload = self._load_payload()
        candidate_key = candidate_id.strip().lower()
        if candidate_key in payload["approved"]:
            return "approved"
        if candidate_key in payload["rejected"]:
            return "rejected"
        return "unknown"

    def approve(self, candidate: CandidateSource, *, note: str = "") -> ApprovalRecord:
        payload = self._load_payload()
        candidate_key = self.candidate_id(candidate)
        now = datetime.now(timezone.utc)

        payload["approved"][candidate_key] = {
            "discoverer": candidate.discoverer,
            "path": candidate.path,
            "app_hint": candidate.app_hint,
            "approved_at": now.isoformat(),
            "user_note": note,
        }
        payload["rejected"].pop(candidate_key, None)
        self._save_payload(payload)

        return ApprovalRecord(
            candidate_id=candidate_key,
            status="approved",
            metadata=_record_metadata(payload["approved"][candidate_key]),
            timestamp=now,
            note=note,
        )

    def reject(self, candidate: CandidateSource, *, reason: str = "user_choice") -> ApprovalRecord:
        payload = self._load_payload()
        candidate_key = self.candidate_id(candidate)
        now = datetime.now(timezone.utc)

        payload["rejected"][candidate_key] = {
            "discoverer": candidate.discoverer,
            "path": candidate.path,
            "app_hint": candidate.app_hint,
            "rejected_at": now.isoformat(),
            "reason": reason,
        }
        payload["approved"].pop(candidate_key, None)
        self._save_payload(payload)

        return ApprovalRecord(
            candidate_id=candidate_key,
            status="rejected",
            metadata=_record_metadata(payload["rejected"][candidate_key]),
            timestamp=now,
            note=reason,
        )

    def unset(self, candidate_id: str) -> None:
        payload = self._load_payload()
        candidate_key = candidate_id.strip().lower()
        removed = payload["approved"].pop(candidate_key, None)
        removed = payload["rejected"].pop(candidate_key, None) or removed
        if removed is not None:
            self._save_payload(payload)

    def list_approved(self) -> list[ApprovalRecord]:
        payload = self._load_payload()
        records: list[ApprovalRecord] = []
        for candidate_key in sorted(payload["approved"].keys()):
            raw_row = payload["approved"][candidate_key]
            row = raw_row if isinstance(raw_row, dict) else {}
            records.append(
                ApprovalRecord(
                    candidate_id=candidate_key,
                    status="approved",
                    metadata=_record_metadata(row),
                    timestamp=_parse_iso_datetime(row.get("approved_at")),
                    note=str(row.get("user_note") or ""),
                )
            )
        return records

    def list_rejected(self) -> list[ApprovalRecord]:
        payload = self._load_payload()
        records: list[ApprovalRecord] = []
        for candidate_key in sorted(payload["rejected"].keys()):
            raw_row = payload["rejected"][candidate_key]
            row = raw_row if isinstance(raw_row, dict) else {}
            records.append(
                ApprovalRecord(
                    candidate_id=candidate_key,
                    status="rejected",
                    metadata=_record_metadata(row),
                    timestamp=_parse_iso_datetime(row.get("rejected_at")),
                    note=str(row.get("reason") or ""),
                )
            )
        return records

    def _load_payload(self) -> dict[str, dict[str, Any] | int]:
        if not self.path.exists():
            return _empty_payload()

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _empty_payload()

        if not isinstance(raw, dict):
            return _empty_payload()

        approved = raw.get("approved")
        rejected = raw.get("rejected")
        version = raw.get("version", 1)

        return {
            "version": 1 if not isinstance(version, int) else version,
            "approved": approved if isinstance(approved, dict) else {},
            "rejected": rejected if isinstance(rejected, dict) else {},
        }

    def _save_payload(self, payload: dict[str, dict[str, Any] | int]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.path)


def _record_metadata(row: dict[str, Any]) -> dict[str, str]:
    return {
        "discoverer": str(row.get("discoverer") or ""),
        "path": str(row.get("path") or ""),
        "app_hint": str(row.get("app_hint") or ""),
    }


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _empty_payload() -> dict[str, dict[str, Any] | int]:
    return {
        "version": 1,
        "approved": {},
        "rejected": {},
    }


__all__ = ["ApprovalRecord", "ApprovalStore"]
