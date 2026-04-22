from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import call

import pytest

from keypulse.capture.camera_monitor import CameraMonitor
from keypulse.capture.manager import CaptureManager
from keypulse.config import Config


class _FakeProcess:
    def __init__(self, lines: list[str], returncode: int = 0):
        self.stdout = iter(lines)
        self._returncode = returncode
        self.killed = False

    def poll(self):
        return self._returncode

    def kill(self):
        self.killed = True


def test_camera_monitor_parses_running_state_and_calls_true(monkeypatch):
    callback = MagicMock()
    process = _FakeProcess(
        [
            'timestamp subsystem kCMIODevicePropertyDeviceIsRunningSomewhere = 1\n',
        ]
    )
    monkeypatch.setattr("keypulse.capture.camera_monitor.subprocess.Popen", lambda *args, **kwargs: process)

    monitor = CameraMonitor(callback)
    monitor.start()
    monitor._thread.join(timeout=1)
    monitor.stop()

    callback.assert_called_once_with(True)


def test_camera_monitor_parses_release_and_calls_false(monkeypatch):
    callback = MagicMock()
    process = _FakeProcess(
        [
            'timestamp subsystem kCMIODevicePropertyDeviceIsRunningSomewhere = 1\n',
            'timestamp subsystem kCMIODevicePropertyDeviceIsRunningSomewhere = 0\n',
        ]
    )
    monkeypatch.setattr("keypulse.capture.camera_monitor.subprocess.Popen", lambda *args, **kwargs: process)

    monitor = CameraMonitor(callback)
    monitor.start()
    monitor._thread.join(timeout=1)
    monitor.stop()

    assert callback.call_args_list == [call(True), call(False)]


def test_camera_monitor_uses_reference_count_for_multiple_devices(monkeypatch):
    callback = MagicMock()
    process = _FakeProcess(
        [
            "a kCMIODevicePropertyDeviceIsRunningSomewhere = 1\n",
            "b kCMIODevicePropertyDeviceIsRunningSomewhere = 1\n",
            "c kCMIODevicePropertyDeviceIsRunningSomewhere = 0\n",
            "d kCMIODevicePropertyDeviceIsRunningSomewhere = 0\n",
        ]
    )
    monkeypatch.setattr("keypulse.capture.camera_monitor.subprocess.Popen", lambda *args, **kwargs: process)

    monitor = CameraMonitor(callback)
    monitor.start()
    monitor._thread.join(timeout=1)
    monitor.stop()

    assert callback.call_args_list == [call(True), call(False)]


def test_camera_monitor_swallows_subprocess_start_failure(monkeypatch):
    monkeypatch.setattr(
        "keypulse.capture.camera_monitor.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")),
    )

    monitor = CameraMonitor(MagicMock())
    monitor.start()
    monitor._thread.join(timeout=1)
    monitor.stop()


def test_manager_pause_and_resume_only_existing_watchers():
    manager = CaptureManager(Config())
    ax_text = MagicMock()
    ocr = MagicMock()
    manager._watchers = {"ax_text": ax_text, "ocr": ocr}

    manager.pause_watchers(["ax_text", "missing"])
    manager.resume_watchers(["ocr", "missing"])

    ax_text.pause.assert_called_once_with()
    ax_text.resume.assert_not_called()
    ocr.pause.assert_not_called()
    ocr.resume.assert_called_once_with()


def test_manager_camera_monitor_wiring_pauses_and_resumes_watchers(monkeypatch):
    callbacks = {}

    class _FakeCameraMonitor:
        def __init__(self, on_change):
            callbacks["on_change"] = on_change
            callbacks["started"] = False

        def start(self):
            callbacks["started"] = True

        def stop(self):
            callbacks["stopped"] = True

    manager = CaptureManager(
        Config.model_validate(
            {
                "watchers": {
                    "window": False,
                    "idle": False,
                    "clipboard": False,
                    "manual": False,
                    "browser": False,
                    "ax_text": True,
                    "keyboard_chunk": False,
                    "ocr": True,
                }
            }
        )
    )
    ax_text = MagicMock()
    ocr = MagicMock()
    manager._watchers = {"ax_text": ax_text, "ocr": ocr}
    monkeypatch.setattr("keypulse.capture.manager.CameraMonitor", _FakeCameraMonitor)

    manager._start_camera_monitor()

    assert callbacks["started"] is True
    callbacks["on_change"](True)
    callbacks["on_change"](False)

    ax_text.pause.assert_called_once_with()
    ocr.pause.assert_called_once_with()
    ax_text.resume.assert_called_once_with()
    ocr.resume.assert_called_once_with()
