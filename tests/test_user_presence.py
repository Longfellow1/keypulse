"""Tests for user presence detection module."""

import sys
import pytest
from unittest.mock import patch, MagicMock

from keypulse.capture.user_presence import idle_seconds, is_user_present


class TestIdleSeconds:
    """Test idle_seconds() function."""

    def test_idle_seconds_valid_output(self):
        """Parse valid ioreg output with HIDIdleTime."""
        mock_output = '''
        {
            "IOHIDSystem" = {
                "HIDIdleTime" = 123456789
            }
        }
        '''
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=mock_output
            )
            result = idle_seconds()
            assert result is not None
            assert 0.123 < result < 0.125  # ~0.1235 seconds

    def test_idle_seconds_multiple_devices(self):
        """Return minimum idle time across devices."""
        mock_output = '''
        "HIDIdleTime" = 100000000
        "HIDIdleTime" = 50000000
        "HIDIdleTime" = 200000000
        '''
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=mock_output
            )
            result = idle_seconds()
            assert result is not None
            assert 0.049 < result < 0.051  # ~0.05 seconds (minimum)

    def test_idle_seconds_ioreg_failure(self):
        """Return None on ioreg command failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = idle_seconds()
            assert result is None

    def test_idle_seconds_no_match(self):
        """Return None when HIDIdleTime not found."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="no idle time here"
            )
            result = idle_seconds()
            assert result is None

    def test_idle_seconds_exception(self):
        """Return None on any exception (fail-open)."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("subprocess error")
            result = idle_seconds()
            assert result is None

    def test_idle_seconds_timeout(self):
        """Return None on subprocess timeout."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = TimeoutError()
            result = idle_seconds()
            assert result is None

    @pytest.mark.skipif(sys.platform != "darwin", reason="ioreg only on macOS")
    def test_idle_seconds_real_call(self):
        """On macOS, ioreg returns a float or None."""
        result = idle_seconds()
        # Just verify it's either None or a non-negative float
        assert result is None or isinstance(result, float)
        if result is not None:
            assert result >= 0.0


class TestIsUserPresent:
    """Test is_user_present() function."""

    def test_user_present_below_threshold(self):
        """Return True when idle < threshold."""
        with patch("keypulse.capture.user_presence.idle_seconds") as mock_idle:
            mock_idle.return_value = 100.0
            result = is_user_present(threshold_seconds=200.0)
            assert result is True

    def test_user_not_present_above_threshold(self):
        """Return False when idle >= threshold."""
        with patch("keypulse.capture.user_presence.idle_seconds") as mock_idle:
            mock_idle.return_value = 400.0
            result = is_user_present(threshold_seconds=300.0)
            assert result is False

    def test_user_present_default_threshold(self):
        """Test with default 300-second threshold."""
        with patch("keypulse.capture.user_presence.idle_seconds") as mock_idle:
            mock_idle.return_value = 100.0
            result = is_user_present()
            assert result is True

    def test_user_not_present_default_threshold(self):
        """Test False with default 300-second threshold."""
        with patch("keypulse.capture.user_presence.idle_seconds") as mock_idle:
            mock_idle.return_value = 400.0
            result = is_user_present()
            assert result is False

    def test_user_present_idle_none(self):
        """Return True when idle_seconds returns None (fail-open)."""
        with patch("keypulse.capture.user_presence.idle_seconds") as mock_idle:
            mock_idle.return_value = None
            result = is_user_present(threshold_seconds=0.0)
            assert result is True

    def test_user_present_zero_threshold(self):
        """Test with zero threshold (any idle = absent)."""
        with patch("keypulse.capture.user_presence.idle_seconds") as mock_idle:
            mock_idle.return_value = 0.1
            result = is_user_present(threshold_seconds=0.0)
            assert result is False

    def test_user_present_boundary(self):
        """Test exact threshold boundary."""
        with patch("keypulse.capture.user_presence.idle_seconds") as mock_idle:
            mock_idle.return_value = 300.0
            result = is_user_present(threshold_seconds=300.0)
            assert result is False  # >= threshold means absent
