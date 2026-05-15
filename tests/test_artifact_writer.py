from __future__ import annotations

from pathlib import Path

import pytest

from keypulse.pipeline.artifact_writer import ArtifactCorruptionError, write_artifact
from keypulse.pipeline.run_record import RunRecorder


def _recorder(tmp_path: Path) -> RunRecorder:
    return RunRecorder(
        date_str="2026-05-15",
        kind="daily",
        trigger="manual",
        db_path=tmp_path / "keypulse.db",
        records_dir=tmp_path / "run_records",
    )


def test_write_artifact_happy_path(tmp_path: Path) -> None:
    recorder = _recorder(tmp_path)
    target = tmp_path / "daily.md"

    write_artifact(recorder, target, "# Hello\n", stage="persist_daily_markdown")

    assert target.read_text(encoding="utf-8") == "# Hello\n"
    assert recorder.artifact_paths["obsidian_md"] == str(target)
    checksum = recorder.artifact_checksums["obsidian_md"]
    assert checksum["content_length"] == len("# Hello\n".encode("utf-8"))
    assert checksum["verified"] is True


def test_write_artifact_hash_mismatch_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _recorder(tmp_path)
    target = tmp_path / "daily.md"
    original_read_text = Path.read_text

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if self == target:
            return "CORRUPTED"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    with pytest.raises(ArtifactCorruptionError):
        write_artifact(recorder, target, "# Hello\n", stage="persist_daily_markdown")

    assert recorder.stage_status["artifact_corruption"] == "failed"
