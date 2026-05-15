from __future__ import annotations

import hashlib
from pathlib import Path

from keypulse.pipeline.run_record import RunRecorder
from keypulse.utils.atomic_io import atomic_write_text


class ArtifactCorruptionError(RuntimeError):
    """Raised when persisted artifact content differs from the pre-write hash."""


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _artifact_key_for_path(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return "daily_summary_json"
    return "obsidian_md"


def write_artifact(recorder: RunRecorder, path: Path, content: str, *, stage: str) -> None:
    """Atomic write + hash verify, with run_record bookkeeping."""
    target = Path(path).expanduser()
    body = str(content)
    sha256 = _sha256_text(body)
    content_length = len(body.encode("utf-8"))
    artifact_key = _artifact_key_for_path(target)

    recorder.set_artifact_paths(**{artifact_key: str(target)})
    recorder.set_artifact_checksum(
        artifact_key=artifact_key,
        stage=stage,
        path=str(target),
        sha256=sha256,
        content_length=content_length,
        verified=False,
    )

    atomic_write_text(target, body, encoding="utf-8")

    persisted = target.read_text(encoding="utf-8")
    persisted_sha = _sha256_text(persisted)
    if persisted_sha != sha256:
        recorder.mark_stage(
            "artifact_corruption",
            "failed",
            reason=f"{stage}_hash_mismatch",
            error_class="ArtifactCorruptionError",
        )
        raise ArtifactCorruptionError(f"artifact hash mismatch for {target}")

    recorder.set_artifact_checksum(
        artifact_key=artifact_key,
        stage=stage,
        path=str(target),
        sha256=sha256,
        content_length=content_length,
        verified=True,
    )
