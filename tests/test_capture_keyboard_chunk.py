from __future__ import annotations

import queue

from keypulse.capture.watchers.keyboard_chunk import (
    KeyboardChunkBuffer,
    KeyboardChunkWatcher,
    _normalize_buffered_text,
)


def test_keyboard_chunk_buffer_flushes_after_silence():
    buffer = KeyboardChunkBuffer(silence_sec=2.0, force_flush_sec=2.0)

    buffer.add_text("hello", app_name="Notes", window_title="Draft", now=0.0)
    buffer.add_text(" world", app_name="Notes", window_title="Draft", now=1.0)

    event = buffer.flush_if_due(now=2.5)

    assert event is not None
    assert event.source == "keyboard_chunk"
    assert event.content_text == "hello world"


def test_keyboard_chunk_buffer_drops_empty_and_whitespace_only_chunks():
    buffer = KeyboardChunkBuffer(silence_sec=2.0, force_flush_sec=2.0)

    buffer.add_text("   ", app_name="Notes", window_title="Draft", now=0.0)

    assert buffer.flush_if_due(now=3.0) is None


class _StubSource:
    def __init__(self):
        self.callback = None
        self.started = False
        self.stopped = False

    def start(self, callback):
        self.started = True
        self.callback = callback
        return True

    def stop(self):
        self.stopped = True


def test_keyboard_chunk_watcher_buffers_text_from_native_source():
    source = _StubSource()
    watcher = KeyboardChunkWatcher(
        queue.Queue(),
        silence_sec=2.0,
        force_flush_sec=2.0,
        source=source,
    )

    watcher.start()
    assert source.started is True
    assert source.callback is not None

    source.callback(
        "hello",
        app_name="Notes",
        window_title="Draft",
        process_name="com.apple.Notes",
        now=1.0,
    )
    source.callback(
        " world",
        app_name="Notes",
        window_title="Draft",
        process_name="com.apple.Notes",
        now=2.0,
    )

    event = watcher._buffer.flush_if_due(now=4.5)
    watcher.stop()

    assert event is not None
    assert event.content_text == "hello world"
    assert event.app_name == "Notes"
    assert event.window_title == "Draft"
    assert source.stopped is True


def test_keyboard_chunk_watcher_safely_ignores_secure_input_from_source():
    source = _StubSource()
    watcher = KeyboardChunkWatcher(
        queue.Queue(),
        silence_sec=2.0,
        force_flush_sec=2.0,
        source=source,
    )

    watcher.start()
    source.callback(
        "secret",
        app_name="Terminal",
        window_title="Password",
        process_name="com.apple.Terminal",
        now=1.0,
        secure_input=True,
    )
    watcher.stop()

    assert watcher._buffer.flush_if_due(now=4.0) is None


def test_keyboard_chunk_normalization_applies_backspaces_and_removes_control_chars():
    text = _normalize_buffered_text(["ab", "c\b", "\x01", "d", "\x7f", "e"])

    assert text == "abe"


def test_keyboard_chunk_buffer_force_flushes_after_two_and_half_seconds():
    buffer = KeyboardChunkBuffer(silence_sec=2.0, force_flush_sec=2.0)

    buffer.add_text("hello", app_name="Notes", window_title="Draft", now=0.0)
    buffer.add_text(" world", app_name="Notes", window_title="Draft", now=1.0)

    event = buffer.flush_if_due(now=2.5)

    assert event is not None
    assert event.content_text == "hello world"
