"""Tests for T1 trigger gating in obsidian sync command."""

import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from click.testing import CliRunner

from keypulse.cli import obsidian_sync


@pytest.fixture
def runner():
    """Provide a Click CLI runner."""
    return CliRunner()


@pytest.fixture
def mock_config(tmp_path):
    """Provide a mock config with db_path_expanded."""
    cfg = MagicMock()
    cfg.db_path_expanded = tmp_path / "keypulse.db"
    cfg.obsidian.vault_name = "test-vault"
    return cfg


class TestObsidianSyncGating:
    """T1 trigger gating tests for obsidian sync."""

    def test_skipped_when_no_activity(self, runner, mock_config):
        """Should skip sync and emit skipped log when should_trigger returns False."""
        with patch("keypulse.cli.get_config", return_value=mock_config), \
             patch("keypulse.cli.require_db"), \
             patch("keypulse.cli.should_trigger", return_value=(False, "no_activity")), \
             patch("keypulse.cli.record_trigger") as mock_record, \
             patch("keypulse.cli._sync_obsidian_bundle") as mock_sync:

            result = runner.invoke(obsidian_sync, [])

            # Should not call _sync_obsidian_bundle
            mock_sync.assert_not_called()

            # Should record skipped outcome
            assert mock_record.call_count >= 1
            first_call = mock_record.call_args_list[0]
            assert first_call[1]["outcome"].startswith("skipped:")

            # Should emit skipped message
            assert "skipped" in result.output.lower()
            assert result.exit_code == 0

    def test_runs_when_activity_present(self, runner, mock_config):
        """Should run sync when should_trigger returns True."""
        with patch("keypulse.cli.get_config", return_value=mock_config), \
             patch("keypulse.cli.require_db"), \
             patch("keypulse.cli.should_trigger", return_value=(True, "")), \
             patch("keypulse.cli.record_trigger") as mock_record, \
             patch("keypulse.cli._sync_obsidian_bundle", return_value=(5, "/path/out", "obsidian")):

            result = runner.invoke(obsidian_sync, [])

            # Should record allowed outcome followed by ran:ok
            outcomes = [call_args[1]["outcome"] for call_args in mock_record.call_args_list]
            assert "allowed" in outcomes
            assert any("ran:ok" in o for o in outcomes)

            # Should succeed
            assert result.exit_code == 0

    def test_bypasses_gate_with_yesterday_flag(self, runner, mock_config):
        """Should bypass T1 gate when --yesterday is specified."""
        with patch("keypulse.cli.get_config", return_value=mock_config), \
             patch("keypulse.cli.require_db"), \
             patch("keypulse.cli.should_trigger") as mock_should, \
             patch("keypulse.cli.record_trigger") as mock_record, \
             patch("keypulse.cli._sync_obsidian_bundle", return_value=(5, "/path/out", "obsidian")):

            result = runner.invoke(obsidian_sync, ["--yesterday"])

            # should_trigger should not be called
            mock_should.assert_not_called()

            # record_trigger should not be called (no T1 gating)
            mock_record.assert_not_called()

            # Should succeed
            assert result.exit_code == 0

    def test_records_run_failure(self, runner, mock_config):
        """Should record ran:fail outcome when sync raises exception."""
        with patch("keypulse.cli.get_config", return_value=mock_config), \
             patch("keypulse.cli.require_db"), \
             patch("keypulse.cli.should_trigger", return_value=(True, "")), \
             patch("keypulse.cli.record_trigger") as mock_record, \
             patch("keypulse.cli._sync_obsidian_bundle", side_effect=ValueError("sync failed")):

            result = runner.invoke(obsidian_sync, [])

            # Should record allowed then ran:fail
            outcomes = [call_args[1]["outcome"] for call_args in mock_record.call_args_list]
            assert "allowed" in outcomes
            assert any("ran:fail" in o for o in outcomes)

            # Should exit with error
            assert result.exit_code != 0

    def test_records_with_correct_trigger_kind(self, runner, mock_config):
        """All recorded outcomes should use kind='T1'."""
        with patch("keypulse.cli.get_config", return_value=mock_config), \
             patch("keypulse.cli.require_db"), \
             patch("keypulse.cli.should_trigger", return_value=(True, "")), \
             patch("keypulse.cli.record_trigger") as mock_record, \
             patch("keypulse.cli._sync_obsidian_bundle", return_value=(5, "/path/out", "obsidian")):

            result = runner.invoke(obsidian_sync, [])

            # All record_trigger calls should have kind='T1'
            for call_args in mock_record.call_args_list:
                assert call_args[0][0] == "T1"

            assert result.exit_code == 0
