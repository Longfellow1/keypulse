from __future__ import annotations

import json
import logging
import queue
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from keypulse.capture.normalizer import normalize_browser_tab_event
from keypulse.capture.policy import redact_url
from keypulse.capture.watchers.browser import BrowserWatcher
from keypulse.config import Config


SUPPORTED_BROWSERS = ["Safari", "Google Chrome", "Arc", "Brave Browser", "Microsoft Edge"]


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _make_watcher() -> BrowserWatcher:
    return BrowserWatcher(queue.Queue(), poll_interval_sec=1.0, supported_browsers=SUPPORTED_BROWSERS)


def test_browser_watcher_emits_event_when_browser_tab_changes():
    watcher = _make_watcher()

    with (
        patch("keypulse.capture.watchers.browser._get_frontmost_app_name", return_value="Google Chrome"),
        patch("keypulse.capture.watchers.browser.subprocess.run") as run,
    ):
        run.side_effect = [
            _completed("https://example.com/a\nPage A\n"),
            _completed("https://example.com/b\nPage B\n"),
        ]

        first = watcher.capture_once()
        second = watcher.capture_once()

    assert first is not None
    assert first.source == "browser"
    assert first.event_type == "browser_tab"
    assert first.app_name == "Google Chrome"
    assert first.window_title == "Page A"
    assert first.content_text == "https://example.com/a"
    assert second is not None
    assert second.content_text == "https://example.com/b"
    assert first.content_hash != second.content_hash


def test_browser_watcher_does_not_reemit_same_url():
    watcher = _make_watcher()

    with (
        patch("keypulse.capture.watchers.browser._get_frontmost_app_name", return_value="Safari"),
        patch("keypulse.capture.watchers.browser.subprocess.run") as run,
    ):
        run.side_effect = [
            _completed("https://example.com/a\nPage A\n"),
            _completed("https://example.com/a\nPage A\n"),
        ]

        first = watcher.capture_once()
        second = watcher.capture_once()

    assert first is not None
    assert second is None
    assert run.call_count == 2


def test_browser_watcher_permission_error_warns_and_disables_browser(caplog):
    watcher = _make_watcher()

    with (
        patch("keypulse.capture.watchers.browser._get_frontmost_app_name", return_value="Google Chrome"),
        patch("keypulse.capture.watchers.browser.subprocess.run") as run,
        caplog.at_level(logging.WARNING),
    ):
        run.return_value = _completed(
            stderr="osascript: not authorized to send Apple events to Google Chrome. (-1743)",
            returncode=1,
        )

        first = watcher.capture_once()
        second = watcher.capture_once()

    assert first is None
    assert second is None
    assert run.call_count == 1
    assert any("not authorized" in record.message.lower() for record in caplog.records)


def test_browser_watcher_skips_unsupported_frontmost_app():
    watcher = _make_watcher()

    with (
        patch("keypulse.capture.watchers.browser._get_frontmost_app_name", return_value="Terminal"),
        patch("keypulse.capture.watchers.browser.subprocess.run") as run,
    ):
        event = watcher.capture_once()

    assert event is None
    run.assert_not_called()


def test_browser_url_redaction_masks_sensitive_query_values():
    event = normalize_browser_tab_event(
        url="https://example.com/callback?access_token=abc123&foo=bar&api_key=secret",
        title="Auth",
        browser_name="Safari",
    )

    metadata = json.loads(event.metadata_json or "{}")

    assert redact_url("https://example.com/callback?access_token=abc123&foo=bar&api_key=secret") == (
        "https://example.com/callback?access_token=[REDACTED]&foo=bar&api_key=[REDACTED]"
    )
    assert event.content_text == "https://example.com/callback?access_token=[REDACTED]&foo=bar&api_key=[REDACTED]"
    assert metadata["url"] == event.content_text
    assert metadata["browser_name"] == "Safari"
    assert metadata["tab_hash"]
    assert len(metadata["tab_hash"]) == 12


def test_repo_config_parses_browser_defaults():
    config_path = Path(__file__).resolve().parents[1] / "config.toml"

    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    config = Config.model_validate(data)

    assert config.watchers.browser is False
    assert config.browser.poll_interval_sec == 1.0
    assert config.browser.supported_browsers == SUPPORTED_BROWSERS
    assert config.keyboard_chunk.force_flush_sec == 2.0
