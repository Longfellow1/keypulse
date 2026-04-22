from __future__ import annotations

import json

from keypulse.capture.provider import OCRRequest
from keypulse.capture.providers.vision_native import VisionNativeOCRProvider
from keypulse.capture.watchers.ocr import OCRContext, OCRTriggerGate, OCRWatcher


class StubUnavailableProvider:
    name = "vision_native"

    def is_available(self) -> bool:
        return False

    def recognize(self, request: OCRRequest):
        raise AssertionError("recognize should not be called when provider is unavailable")


def test_ocr_trigger_gate_waits_for_quiet_keyboard_when_ax_missing():
    gate = OCRTriggerGate(
        window_switch_delay_sec=0.8,
        stable_interval_sec=10.0,
        keyboard_quiet_sec=2.0,
    )

    gate.note_window_change(now=0.0)
    gate.note_keyboard_activity(now=0.5)

    assert gate.should_trigger(now=1.0, ax_text_available=False, content_signature="sig-a") is None
    assert gate.should_trigger(now=2.6, ax_text_available=False, content_signature="sig-a") == "keyboard_quiet"


def test_ocr_trigger_gate_uses_window_switch_when_ax_missing_and_no_recent_keyboard():
    gate = OCRTriggerGate(
        window_switch_delay_sec=0.8,
        stable_interval_sec=10.0,
        keyboard_quiet_sec=2.0,
    )

    gate.note_window_change(now=0.0)

    assert gate.should_trigger(now=0.7, ax_text_available=False, content_signature="sig-a") is None
    assert gate.should_trigger(now=1.0, ax_text_available=False, content_signature="sig-a") == "window_switch"


def test_ocr_watcher_safely_skips_when_provider_unavailable():
    watcher = OCRWatcher(
        event_queue=None,
        provider=StubUnavailableProvider(),
    )

    assert watcher.capture_once(now=1.0) is None


class StubWorkingProvider:
    name = "vision_native"

    def __init__(self):
        self.calls: list[OCRRequest] = []

    def is_available(self) -> bool:
        return True

    def recognize(self, request: OCRRequest):
        self.calls.append(request)
        from keypulse.capture.provider import OCRResult

        return OCRResult(
            provider=self.name,
            text="recognized text",
            ok=True,
            metadata={"engine": "stub"},
        )


def test_ocr_watcher_accepts_explicit_image_ref_from_caller():
    provider = StubWorkingProvider()
    watcher = OCRWatcher(
        event_queue=None,
        provider=provider,
    )

    watcher.note_keyboard_activity(now=0.5)

    event = watcher.capture_once(
        now=2.6,
        image_ref="/tmp/example.png",
    )

    assert event is not None
    assert event.content_text == "recognized text"
    assert provider.calls[0].image_ref == "/tmp/example.png"
    metadata = json.loads(event.metadata_json)
    assert metadata["provider"] == "vision_native"
    assert metadata["trigger_reason"] == "keyboard_quiet"
    assert metadata["engine"] == "stub"


def test_ocr_watcher_reads_frontmost_context_and_image_when_caller_does_not_pass_image():
    provider = StubWorkingProvider()
    context_calls: list[float] = []
    image_calls = []

    def read_context(now: float) -> OCRContext:
        context_calls.append(now)
        return OCRContext(
            app_name="Notes",
            window_title="Draft",
            process_name="com.apple.Notes",
            ax_text_available=False,
            content_signature="sig-notes",
        )

    def capture_image(context: OCRContext):
        image_calls.append(context)
        return "cgimage:notes"

    watcher = OCRWatcher(
        event_queue=None,
        provider=provider,
        context_reader=read_context,
        image_provider=capture_image,
    )
    watcher.note_keyboard_activity(now=0.5)

    event = watcher.capture_once(now=2.6)

    assert event is not None
    assert event.content_text == "recognized text"
    assert context_calls == [2.6]
    assert len(image_calls) == 1
    assert image_calls[0].app_name == "Notes"
    assert provider.calls[0].image_ref == "cgimage:notes"


def test_ocr_watcher_safely_skips_when_frontmost_window_capture_fails():
    provider = StubWorkingProvider()

    watcher = OCRWatcher(
        event_queue=None,
        provider=provider,
        context_reader=lambda now: OCRContext(
            app_name="Preview",
            window_title="Scan",
            process_name="com.apple.Preview",
            ax_text_available=False,
            content_signature="sig-preview",
        ),
        image_provider=lambda context: None,
    )
    watcher.note_keyboard_activity(now=0.5)

    assert watcher.capture_once(now=2.6) is None
    assert provider.calls == []


class _FakeRecognizedText:
    def __init__(self, text: str):
        self._text = text

    def string(self) -> str:
        return self._text


class _FakeObservation:
    def __init__(self, text: str):
        self._text = text

    def topCandidates_(self, _limit: int):
        return [_FakeRecognizedText(self._text)]


class _FakeRequest:
    def __init__(self):
        self._results = [_FakeObservation("hello vision")]

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def results(self):
        return list(self._results)


class _FakeHandler:
    last_image = None

    @classmethod
    def alloc(cls):
        return cls()

    def initWithCGImage_options_(self, image, _options):
        type(self).last_image = image
        return self

    def performRequests_error_(self, requests, _error):
        self._requests = requests
        return True, None


class _FakeVisionModule:
    VNRecognizeTextRequest = _FakeRequest
    VNImageRequestHandler = _FakeHandler


class _FakeQuartzModule:
    @staticmethod
    def NSURL_fileURLWithPath_(path: str):
        return f"url:{path}"

    class CIImage:
        @staticmethod
        def imageWithContentsOfURL_(url: str):
            return f"ci:{url}"

    @staticmethod
    def CGImageSourceCreateWithURL(url: str, _options):
        return f"source:{url}"

    @staticmethod
    def CGImageSourceCreateImageAtIndex(source: str, index: int, _options):
        return f"cg:{source}:{index}"


def test_vision_provider_recognizes_text_from_image_path_with_native_modules():
    provider = VisionNativeOCRProvider(
        vision_module=_FakeVisionModule(),
        quartz_module=_FakeQuartzModule(),
    )

    result = provider.recognize(OCRRequest(image_ref="/tmp/input.png"))

    assert result.ok is True
    assert result.text == "hello vision"
    assert result.reason is None
    assert result.metadata["image_source"] == "path"
    assert _FakeHandler.last_image == "cg:source:url:/tmp/input.png:0"
