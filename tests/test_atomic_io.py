"""Tests for keypulse.utils.atomic_io.atomic_write_text."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from keypulse.utils.atomic_io import atomic_write_text


class TestAtomicWriteText:
    def test_atomic_write_creates_file(self, tmp_path: Path):
        target = tmp_path / "output.md"
        atomic_write_text(target, "hello world")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_atomic_write_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "output.md"
        target.write_text("old content", encoding="utf-8")
        atomic_write_text(target, "new content")
        assert target.read_text(encoding="utf-8") == "new content"

    def test_atomic_write_cleans_tmp_on_failure(self, tmp_path: Path):
        target = tmp_path / "output.md"

        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                atomic_write_text(target, "data")

        # No .tmp.* files should remain
        leftover = list(tmp_path.glob(".tmp.*"))
        assert leftover == [], f"Leftover tmp files: {leftover}"

    def test_atomic_write_accepts_str_path(self, tmp_path: Path):
        target = str(tmp_path / "str_output.md")
        atomic_write_text(target, "via str")
        assert Path(target).read_text(encoding="utf-8") == "via str"
