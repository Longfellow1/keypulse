from __future__ import annotations

from unittest.mock import MagicMock, patch

from keypulse.capture import active_app


class _FakeApp:
    def __init__(self, name: str, pid: int):
        self._name = name
        self._pid = pid

    def localizedName(self) -> str:
        return self._name

    def processIdentifier(self) -> int:
        return self._pid


class TestGetActiveApp:
    def setup_method(self):
        active_app.reset_for_test()

    def test_real_app_returned(self):
        fake_app = _FakeApp("Chrome", 1234)

        with patch("AppKit.NSWorkspace") as mock_workspace_class:
            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_app
            name, pid = active_app.get_active_app()
            assert name == "Chrome"
            assert pid == 1234

    def test_loginwindow_falls_back_to_last_known(self):
        fake_chrome = _FakeApp("Chrome", 1234)
        fake_loginwindow = _FakeApp("loginwindow", 999)

        with patch("AppKit.NSWorkspace") as mock_workspace_class:
            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_chrome
            name, pid = active_app.get_active_app()
            assert name == "Chrome"
            assert pid == 1234

            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_loginwindow
            name, pid = active_app.get_active_app()
            assert name == "Chrome"
            assert pid == 1234

    def test_no_history_returns_none(self):
        active_app.reset_for_test()
        fake_loginwindow = _FakeApp("loginwindow", 999)

        with patch("AppKit.NSWorkspace") as mock_workspace_class:
            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_loginwindow
            name, pid = active_app.get_active_app()
            assert name is None
            assert pid is None

    def test_screensaver_falls_back(self):
        fake_notes = _FakeApp("Notes", 5678)
        fake_screensaver = _FakeApp("ScreenSaverEngine", 999)

        with patch("AppKit.NSWorkspace") as mock_workspace_class:
            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_notes
            name, pid = active_app.get_active_app()
            assert name == "Notes"

            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_screensaver
            name, pid = active_app.get_active_app()
            assert name == "Notes"

    def test_dock_falls_back(self):
        fake_safari = _FakeApp("Safari", 9999)
        fake_dock = _FakeApp("Dock", 999)

        with patch("AppKit.NSWorkspace") as mock_workspace_class:
            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_safari
            name, pid = active_app.get_active_app()
            assert name == "Safari"

            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_dock
            name, pid = active_app.get_active_app()
            assert name == "Safari"

    def test_is_system_overlay_active_true_for_loginwindow(self):
        fake_loginwindow = _FakeApp("loginwindow", 999)

        with patch("AppKit.NSWorkspace") as mock_workspace_class:
            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_loginwindow
            assert active_app.is_system_overlay_active() is True

    def test_is_system_overlay_active_false_for_chrome(self):
        fake_chrome = _FakeApp("Chrome", 1234)

        with patch("AppKit.NSWorkspace") as mock_workspace_class:
            mock_workspace_class.sharedWorkspace.return_value.frontmostApplication.return_value = fake_chrome
            assert active_app.is_system_overlay_active() is False

    def test_exception_handling(self):
        mock_workspace = MagicMock()
        mock_workspace.sharedWorkspace.side_effect = Exception("boom")
        with patch("AppKit.NSWorkspace", mock_workspace):
            name, pid = active_app.get_active_app()
            assert name is None
            assert pid is None

    def test_is_system_overlay_exception_returns_false(self):
        mock_workspace = MagicMock()
        mock_workspace.sharedWorkspace.side_effect = Exception("boom")
        with patch("AppKit.NSWorkspace", mock_workspace):
            assert active_app.is_system_overlay_active() is False
