from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from AppKit import NSURL

from keypulse.capture.watchers.browser import DEFAULT_SUPPORTED_BROWSERS
from keypulse.capture.watchers.browser_discovery import discover_http_handler_apps


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    discover_http_handler_apps.cache_clear()
    yield
    discover_http_handler_apps.cache_clear()


def _make_app_bundle(
    tmp_path: Path,
    bundle_name: str,
    *,
    bundle_display_name: str | None = None,
    bundle_name_value: str | None = None,
    schemes: tuple[str, ...] = ("https",),
) -> Path:
    app_path = tmp_path / f"{bundle_name}.app"
    contents_path = app_path / "Contents"
    contents_path.mkdir(parents=True)

    info: dict[str, object] = {
        "CFBundleIdentifier": f"com.example.{bundle_name.lower().replace(' ', '-')}",
        "CFBundleExecutable": bundle_name.replace(" ", ""),
        "CFBundlePackageType": "APPL",
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "Web site URL",
                "CFBundleURLSchemes": list(schemes),
            }
        ],
    }
    if bundle_display_name is not None:
        info["CFBundleDisplayName"] = bundle_display_name
    if bundle_name_value is not None:
        info["CFBundleName"] = bundle_name_value

    (contents_path / "Info.plist").write_bytes(plistlib.dumps(info))
    return app_path


def test_discover_http_handler_apps_uses_launch_services_urls_and_bundle_names(
    monkeypatch,
    tmp_path,
):
    atlas_app = _make_app_bundle(
        tmp_path,
        "ChatGPT Atlas",
        bundle_display_name="ChatGPT Atlas",
        schemes=("http", "https", "file"),
    )
    chrome_app = _make_app_bundle(
        tmp_path,
        "Google Chrome",
        bundle_display_name="Google Chrome",
        bundle_name_value="Chrome",
        schemes=("http", "https", "file"),
    )
    safari_app = _make_app_bundle(
        tmp_path,
        "Safari",
        bundle_display_name="Safari Browser",
        bundle_name_value="Safari",
        schemes=("http", "https", "file"),
    )

    monkeypatch.setattr(
        "keypulse.capture.watchers.browser_discovery._discover_application_urls_from_filesystem",
        lambda: (),
    )
    monkeypatch.setattr(
        "keypulse.capture.watchers.browser_discovery.LSCopyApplicationURLsForURL",
        lambda _url, _role_mask=0xFFFFFFFF: (
            NSURL.fileURLWithPath_(str(atlas_app)),
            NSURL.fileURLWithPath_(str(chrome_app)),
            NSURL.fileURLWithPath_(str(safari_app)),
        ),
    )

    assert discover_http_handler_apps() == (
        "ChatGPT Atlas",
        "Google Chrome",
        "Safari",
    )


def test_discover_http_handler_apps_falls_back_to_default_on_launch_services_failure(
    monkeypatch,
):
    def _boom(_url, _role_mask=0xFFFFFFFF):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "keypulse.capture.watchers.browser_discovery.LSCopyApplicationURLsForURL",
        _boom,
    )
    monkeypatch.setattr(
        "keypulse.capture.watchers.browser_discovery._discover_application_urls_from_filesystem",
        lambda: (),
    )

    assert discover_http_handler_apps() == DEFAULT_SUPPORTED_BROWSERS
