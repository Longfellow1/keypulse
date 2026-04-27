import json
from datetime import datetime, timezone, timedelta

import pytest

from keypulse.pipeline.fragments import (
    SemanticFragment,
    EvidenceSlice,
    extract_fragments,
    aggregate_to_slices,
    render_slices_for_pass1,
    filter_low_quality_slices,
    extract_clean_slices,
    filter_noisy_raw_events,
)


@pytest.fixture
def base_ts():
    return datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)


class TestExtractFragments:
    def test_keyboard_chunk_event(self, base_ts):
        rows = [
            {
                "id": 1,
                "event_type": "keyboard_chunk_capture",
                "ts_utc": base_ts.isoformat(),
                "app_name": "VS Code",
                "window_title": "main.py - keypulse",
                "content_text": "def extract_fragments",
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 1
        assert frags[0].verb == "type"
        assert frags[0].weight == 1.0
        assert frags[0].sample == "def extract_fragments"
        assert frags[0].app == "VS Code"

    def test_clipboard_event(self, base_ts):
        rows = [
            {
                "id": 2,
                "event_type": "clipboard_copy",
                "ts_utc": base_ts.isoformat(),
                "app_name": "终端",
                "window_title": "Terminal Window",
                "content_text": "git commit -m 'implement fragments'",
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 1
        assert frags[0].verb == "paste"
        assert frags[0].weight == 0.9
        assert frags[0].sample == "git commit -m 'implement fragments'"

    def test_ax_text_event(self, base_ts):
        rows = [
            {
                "id": 3,
                "event_type": "ax_text_capture",
                "ts_utc": base_ts.isoformat(),
                "app_name": "Safari",
                "window_title": "Claude - claude.ai",
                "content_text": "semantic fragment extraction",
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 1
        assert frags[0].verb == "type"
        assert frags[0].weight == 0.8

    def test_ocr_event(self, base_ts):
        rows = [
            {
                "id": 4,
                "event_type": "ocr_text_capture",
                "ts_utc": base_ts.isoformat(),
                "app_name": "Chrome",
                "window_title": "Screenshot",
                "content_text": "extracted from screen",
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 1
        assert frags[0].verb == "view"
        assert frags[0].weight == 0.5

    def test_window_focus_event(self, base_ts):
        rows = [
            {
                "id": 5,
                "event_type": "window_focus",
                "ts_utc": base_ts.isoformat(),
                "app_name": "Obsidian",
                "window_title": "My Notes",
                "content_text": None,
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 1
        assert frags[0].verb == "switch"
        assert frags[0].weight == 0.3
        assert frags[0].sample == ""

    def test_payload_as_json_string(self, base_ts):
        payload_json = json.dumps({"text": "json payload content"})
        rows = [
            {
                "id": 6,
                "event_type": "keyboard_chunk_capture",
                "ts_utc": base_ts.isoformat(),
                "app_name": "Editor",
                "window_title": "document.md",
                "content_text": payload_json,
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 1
        assert frags[0].sample == "json payload content"

    def test_privacy_keyword_filtered(self, base_ts):
        rows = [
            {
                "id": 7,
                "event_type": "keyboard_chunk_capture",
                "ts_utc": base_ts.isoformat(),
                "app_name": "Chrome",
                "window_title": "Login Page",
                "content_text": "password123",
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 0

    def test_privacy_keyword_token(self, base_ts):
        rows = [
            {
                "id": 8,
                "event_type": "clipboard_copy",
                "ts_utc": base_ts.isoformat(),
                "app_name": "Terminal",
                "window_title": "shell",
                "content_text": "token=sk-abc123xyz",
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 0

    def test_unrecognized_event_type_skipped(self, base_ts):
        rows = [
            {
                "id": 9,
                "event_type": "unknown_event",
                "ts_utc": base_ts.isoformat(),
                "app_name": "App",
                "window_title": "Window",
                "content_text": "content",
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 0

    def test_invalid_ts_skipped(self, base_ts):
        rows = [
            {
                "id": 10,
                "event_type": "keyboard_chunk_capture",
                "ts_utc": "invalid-timestamp",
                "app_name": "App",
                "window_title": "Window",
                "content_text": "content",
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 0

    def test_sample_truncated_to_80_chars(self, base_ts):
        long_text = "def extract_fragments(rows: list[dict]) -> list[SemanticFragment]: return fragments if valid" * 2
        rows = [
            {
                "id": 11,
                "event_type": "keyboard_chunk_capture",
                "ts_utc": base_ts.isoformat(),
                "app_name": "Editor",
                "window_title": "file.py",
                "content_text": long_text,
            }
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 1
        assert len(frags[0].sample) == 80


class TestAggregateToSlices:
    def test_same_app_same_window_merged(self, base_ts):
        frags = [
            SemanticFragment(
                ts=base_ts,
                app="Chrome",
                verb="type",
                object_hint="Search box",
                sample="query1",
                weight=1.0,
                source_id=1,
            ),
            SemanticFragment(
                ts=base_ts + timedelta(seconds=30),
                app="Chrome",
                verb="paste",
                object_hint="Search box",
                sample="query2",
                weight=0.9,
                source_id=2,
            ),
        ]
        slices = aggregate_to_slices(frags, window_seconds=300)
        assert len(slices) == 1
        assert slices[0].app == "Chrome"
        assert slices[0].fragment_count == 2
        assert set(slices[0].verbs) == {"type", "paste"}
        assert len(slices[0].source_ids) == 2

    def test_cross_window_split(self, base_ts):
        frags = [
            SemanticFragment(
                ts=base_ts,
                app="Chrome",
                verb="type",
                object_hint="hint1",
                sample="sample1",
                weight=1.0,
                source_id=1,
            ),
            SemanticFragment(
                ts=base_ts + timedelta(seconds=400),
                app="Chrome",
                verb="type",
                object_hint="hint2",
                sample="sample2",
                weight=1.0,
                source_id=2,
            ),
        ]
        slices = aggregate_to_slices(frags, window_seconds=300)
        assert len(slices) == 2
        assert slices[0].fragment_count == 1
        assert slices[1].fragment_count == 1

    def test_cross_app_split(self, base_ts):
        frags = [
            SemanticFragment(
                ts=base_ts,
                app="Chrome",
                verb="type",
                object_hint="hint1",
                sample="sample1",
                weight=1.0,
                source_id=1,
            ),
            SemanticFragment(
                ts=base_ts + timedelta(seconds=30),
                app="Safari",
                verb="type",
                object_hint="hint2",
                sample="sample2",
                weight=1.0,
                source_id=2,
            ),
        ]
        slices = aggregate_to_slices(frags, window_seconds=300)
        assert len(slices) == 2
        assert slices[0].app == "Chrome"
        assert slices[1].app == "Safari"

    def test_samples_ordered_by_weight_limited_to_3(self, base_ts):
        frags = [
            SemanticFragment(
                ts=base_ts,
                app="Chrome",
                verb="type",
                object_hint="hint",
                sample="low_weight",
                weight=0.5,
                source_id=1,
            ),
            SemanticFragment(
                ts=base_ts + timedelta(seconds=10),
                app="Chrome",
                verb="type",
                object_hint="hint",
                sample="high_weight",
                weight=1.0,
                source_id=2,
            ),
            SemanticFragment(
                ts=base_ts + timedelta(seconds=20),
                app="Chrome",
                verb="type",
                object_hint="hint",
                sample="mid_weight",
                weight=0.8,
                source_id=3,
            ),
            SemanticFragment(
                ts=base_ts + timedelta(seconds=30),
                app="Chrome",
                verb="type",
                object_hint="hint",
                sample="another_low",
                weight=0.3,
                source_id=4,
            ),
        ]
        slices = aggregate_to_slices(frags, window_seconds=300)
        assert len(slices) == 1
        assert len(slices[0].samples) == 3
        assert slices[0].samples[0] == "high_weight"
        assert slices[0].samples[1] == "mid_weight"
        assert slices[0].samples[2] == "low_weight"

    def test_object_hints_deduplicated_limited_to_5(self, base_ts):
        frags = [
            SemanticFragment(
                ts=base_ts + timedelta(seconds=i),
                app="Editor",
                verb="type",
                object_hint=f"hint{i % 3}",
                sample=f"sample{i}",
                weight=1.0,
                source_id=i,
            )
            for i in range(10)
        ]
        slices = aggregate_to_slices(frags, window_seconds=300)
        assert len(slices) == 1
        assert len(slices[0].object_hints) <= 5

    def test_empty_fragments_returns_empty_slices(self):
        slices = aggregate_to_slices([], window_seconds=300)
        assert len(slices) == 0

    def test_weight_sum_calculated(self, base_ts):
        frags = [
            SemanticFragment(
                ts=base_ts,
                app="App",
                verb="type",
                object_hint="hint",
                sample="s1",
                weight=0.5,
                source_id=1,
            ),
            SemanticFragment(
                ts=base_ts + timedelta(seconds=10),
                app="App",
                verb="type",
                object_hint="hint",
                sample="s2",
                weight=1.0,
                source_id=2,
            ),
        ]
        slices = aggregate_to_slices(frags, window_seconds=300)
        assert slices[0].weight_sum == 1.5


class TestRenderSlicesForPass1:
    def test_basic_render_single_verb(self, base_ts):
        slices = [
            EvidenceSlice(
                ts_start=base_ts,
                ts_end=base_ts + timedelta(seconds=30),
                app="Chrome",
                verbs=["type"],
                object_hints=["Search box"],
                samples=["search query"],
                fragment_count=1,
                weight_sum=1.0,
                source_ids=[1],
            )
        ]
        rendered = render_slices_for_pass1(slices)
        assert len(rendered) == 1
        assert "Chrome" in rendered[0]
        assert "10:00-10:00" in rendered[0]
        assert "type" in rendered[0]
        assert "Search box" in rendered[0]
        assert "search query" in rendered[0]

    def test_render_multiple_verbs(self, base_ts):
        slices = [
            EvidenceSlice(
                ts_start=base_ts,
                ts_end=base_ts + timedelta(seconds=60),
                app="Editor",
                verbs=["type", "paste"],
                object_hints=["file.py"],
                samples=["import sys", "code snippet"],
                fragment_count=2,
                weight_sum=1.9,
                source_ids=[1, 2],
            )
        ]
        rendered = render_slices_for_pass1(slices)
        assert len(rendered) == 1
        line = rendered[0]
        assert "Editor" in line
        assert "paste" in line or "type" in line
        assert "file.py" in line

    def test_render_multiple_hints(self, base_ts):
        slices = [
            EvidenceSlice(
                ts_start=base_ts,
                ts_end=base_ts + timedelta(seconds=30),
                app="Safari",
                verbs=["view"],
                object_hints=["URL example.com", "URL google.com"],
                samples=["screenshot content"],
                fragment_count=2,
                weight_sum=1.0,
                source_ids=[1, 2],
            )
        ]
        rendered = render_slices_for_pass1(slices)
        assert len(rendered) == 1
        line = rendered[0]
        assert "Safari" in line
        assert "example.com" in line
        assert "google.com" in line

    def test_render_empty_samples(self, base_ts):
        slices = [
            EvidenceSlice(
                ts_start=base_ts,
                ts_end=base_ts + timedelta(seconds=30),
                app="Terminal",
                verbs=["switch"],
                object_hints=["shell"],
                samples=[],
                fragment_count=1,
                weight_sum=0.3,
                source_ids=[1],
            )
        ]
        rendered = render_slices_for_pass1(slices)
        assert len(rendered) == 1
        line = rendered[0]
        assert "Terminal" in line
        assert "switch" in line
        assert "shell" in line

    def test_render_empty_hints(self, base_ts):
        slices = [
            EvidenceSlice(
                ts_start=base_ts,
                ts_end=base_ts + timedelta(seconds=30),
                app="Slack",
                verbs=["type"],
                object_hints=[],
                samples=["message text"],
                fragment_count=1,
                weight_sum=0.9,
                source_ids=[1],
            )
        ]
        rendered = render_slices_for_pass1(slices)
        assert len(rendered) == 1
        line = rendered[0]
        assert "Slack" in line
        assert "message text" in line

    def test_render_multiple_slices(self, base_ts):
        slices = [
            EvidenceSlice(
                ts_start=base_ts,
                ts_end=base_ts + timedelta(seconds=30),
                app="Chrome",
                verbs=["type"],
                object_hints=["hint1"],
                samples=["sample1"],
                fragment_count=1,
                weight_sum=1.0,
                source_ids=[1],
            ),
            EvidenceSlice(
                ts_start=base_ts + timedelta(seconds=600),
                ts_end=base_ts + timedelta(seconds=630),
                app="Safari",
                verbs=["view"],
                object_hints=["hint2"],
                samples=["sample2"],
                fragment_count=1,
                weight_sum=0.5,
                source_ids=[2],
            ),
        ]
        rendered = render_slices_for_pass1(slices)
        assert len(rendered) == 2
        assert "Chrome" in rendered[0]
        assert "Safari" in rendered[1]


class TestEndToEndWithRealData:
    def test_real_event_keyboard_to_render(self, base_ts):
        rows = [
            {
                "id": 101,
                "event_type": "keyboard_chunk_capture",
                "ts_utc": base_ts.isoformat(),
                "app_name": "Code",
                "window_title": "fragments.py - keypulse",
                "content_text": "def extract_fragments",
            },
            {
                "id": 102,
                "event_type": "keyboard_chunk_capture",
                "ts_utc": (base_ts + timedelta(seconds=10)).isoformat(),
                "app_name": "Code",
                "window_title": "fragments.py - keypulse",
                "content_text": "(rows: list[dict])",
            },
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 2

        slices = aggregate_to_slices(frags, window_seconds=300)
        assert len(slices) == 1
        assert slices[0].app == "VS Code"
        assert slices[0].fragment_count == 2

        rendered = render_slices_for_pass1(slices)
        assert len(rendered) == 1
        line = rendered[0]
        assert "VS Code" in line
        assert "def extract_fragments" in line or "(rows: list[dict])" in line

    def test_real_event_clipboard_workflow(self, base_ts):
        rows = [
            {
                "id": 201,
                "event_type": "clipboard_copy",
                "ts_utc": base_ts.isoformat(),
                "app_name": "终端",
                "window_title": "Terminal",
                "content_text": "git commit -m 'stage: fragments.py'",
            },
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 1
        assert frags[0].verb == "paste"

        slices = aggregate_to_slices(frags)
        assert len(slices) == 1
        rendered = render_slices_for_pass1(slices)
        assert "git commit" in rendered[0]

    def test_mixed_event_types_single_app(self, base_ts):
        rows = [
            {
                "id": 301,
                "event_type": "keyboard_chunk_capture",
                "ts_utc": base_ts.isoformat(),
                "app_name": "Chrome",
                "window_title": "Claude - claude.ai",
                "content_text": "implement fragments",
            },
            {
                "id": 302,
                "event_type": "clipboard_copy",
                "ts_utc": (base_ts + timedelta(seconds=15)).isoformat(),
                "app_name": "Chrome",
                "window_title": "Claude - claude.ai",
                "content_text": "test code snippet",
            },
            {
                "id": 303,
                "event_type": "window_focus",
                "ts_utc": (base_ts + timedelta(seconds=30)).isoformat(),
                "app_name": "Chrome",
                "window_title": "GitHub - keypulse",
                "content_text": None,
            },
        ]
        frags = extract_fragments(rows)
        assert len(frags) == 3

        slices = aggregate_to_slices(frags, window_seconds=300)
        assert len(slices) == 1
        assert set(slices[0].verbs) == {"type", "paste", "switch"}
        assert len(slices[0].samples) >= 2


class TestHygieneFilters:
    """L1 / L2 / L3 / L4 sanity filters."""

    @pytest.fixture
    def ts(self):
        return datetime(2026, 4, 25, 10, 0, 0, tzinfo=timezone.utc)

    def _row(self, ts, content, event_type="keyboard_chunk_capture",
             app_name="Finder", window_title="some window", row_id=1):
        return {
            "id": row_id,
            "event_type": event_type,
            "ts_utc": ts.isoformat(),
            "app_name": app_name,
            "window_title": window_title,
            "content_text": content,
        }

    # ── L1 ──────────────────────────────────────────────────────────────────

    def test_l1_too_short_dropped(self, ts):
        frags = extract_fragments([self._row(ts, "jx")])
        assert len(frags) == 0

    def test_l1_whitelist_kept(self, ts):
        frags = extract_fragments([self._row(ts, "ok")])
        assert len(frags) == 1
        assert frags[0].sample == "ok"

    def test_l1_4char_non_whitelist_dropped(self, ts):
        frags = extract_fragments([self._row(ts, "abcd")])
        assert len(frags) == 0

    def test_l1_view_verb_not_filtered(self, ts):
        # OCR short text: verb=view, L1 should not apply
        row = self._row(ts, "jx", event_type="ocr_text_capture")
        frags = extract_fragments([row])
        assert len(frags) == 1

    # ── L2 ──────────────────────────────────────────────────────────────────

    def test_l2_low_entropy_dropped(self, ts):
        # "aaaaaa" — single character, entropy = 0
        frags = extract_fragments([self._row(ts, "aaaaaa")])
        assert len(frags) == 0

    def test_l2_low_entropy_qwerty_dropped(self, ts):
        # qwerty: 6 distinct chars, H = log2(6) ≈ 2.58 < 3.5
        frags = extract_fragments([self._row(ts, "qwerty")])
        assert len(frags) == 0

    def test_l2_normal_text_kept(self, ts):
        frags = extract_fragments([self._row(ts, "def extract_fragments")])
        assert len(frags) == 1
        assert frags[0].sample == "def extract_fragments"

    # ── L3 ──────────────────────────────────────────────────────────────────

    def test_l3_login_window_blocks_all(self, ts):
        row = self._row(ts, "some text", window_title="loginwindow")
        frags = extract_fragments([row])
        assert len(frags) == 0

    def test_l3_chinese_login(self, ts):
        row = self._row(ts, "some text", window_title="登录 - 微信")
        frags = extract_fragments([row])
        assert len(frags) == 0

    def test_l3_1password_blocked(self, ts):
        row = self._row(ts, "some text", app_name="1Password")
        frags = extract_fragments([row])
        assert len(frags) == 0

    # ── L4 ──────────────────────────────────────────────────────────────────

    def _make_slice(self, ts, samples, verbs=None):
        if verbs is None:
            verbs = ["type"]
        return EvidenceSlice(
            ts_start=ts,
            ts_end=ts + timedelta(seconds=10),
            app="Finder",
            verbs=verbs,
            object_hints=[],
            samples=samples,
            fragment_count=len(samples),
            weight_sum=float(len(samples)),
            source_ids=list(range(len(samples))),
        )

    def test_l4_repeated_short_edits_dropped(self, ts):
        s = self._make_slice(ts, ["jx", "jix", "jixu"])
        result = filter_low_quality_slices([s])
        assert len(result) == 0

    def test_l4_long_text_kept(self, ts):
        # total length >= 15, so kept even if distance is small
        s = self._make_slice(ts, ["这是一段较长的输入A", "这是一段较长的输入B"])
        result = filter_low_quality_slices([s])
        assert len(result) == 1

    def test_l4_distinct_samples_kept(self, ts):
        s = self._make_slice(ts, ["hello", "world"])
        result = filter_low_quality_slices([s])
        assert len(result) == 1

    # ── End-to-end real noise fixture ───────────────────────────────────────

    def test_real_noise_fixture_end_to_end(self, ts):
        rows = [
            self._row(ts, "jx", row_id=1),
            self._row(ts + timedelta(seconds=5), "jixjixu", row_id=2),
            self._row(ts + timedelta(seconds=10), "123", row_id=3),
            self._row(ts + timedelta(seconds=15), "jx", row_id=4),
        ]
        result = extract_clean_slices(rows)
        assert result == []


class TestFilterNoisyRawEvents:
    @pytest.fixture
    def base_ts(self):
        return datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)

    def _row(
        self,
        ts: datetime,
        content_text: str = "",
        event_type: str = "keyboard_chunk_capture",
        window_title: str = "Test Window",
        app_name: str = "Test App",
        row_id: int = 1,
    ) -> dict:
        return {
            "id": row_id,
            "event_type": event_type,
            "ts_start": ts.isoformat(),
            "ts_utc": ts.isoformat(),
            "window_title": window_title,
            "app_name": app_name,
            "content_text": content_text,
        }

    # ── L3: login context ───────────────────────────────────────────────────

    def test_l3_login_window_row_dropped(self, base_ts):
        rows = [
            self._row(base_ts, "anything", window_title="Login Page"),
            self._row(base_ts + timedelta(seconds=1), "design product spec", window_title="Normal"),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1
        assert result[0]["window_title"] == "Normal"

    def test_l3_1password_app_dropped(self, base_ts):
        rows = [
            self._row(base_ts, "pwd123", app_name="1Password"),
            self._row(base_ts + timedelta(seconds=1), "design doc", app_name="Notion"),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1
        assert result[0]["app_name"] == "Notion"

    def test_l3_loginwindow_lowercase(self, base_ts):
        rows = [
            self._row(base_ts, "", window_title="loginwindow"),
            self._row(base_ts + timedelta(seconds=1), "design doc here", window_title="editor"),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1
        assert "editor" in result[0]["window_title"]

    # ── L1: short input ────────────────────────────────────────────────────

    def test_l1_short_keyboard_dropped(self, base_ts):
        rows = [
            self._row(base_ts, "jx", event_type="keyboard_chunk_capture"),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 0

    def test_l1_short_whitelist_kept(self, base_ts):
        rows = [
            self._row(base_ts, "ok", event_type="keyboard_chunk_capture"),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1
        assert result[0]["content_text"] == "ok"

    # ── L2: entropy ────────────────────────────────────────────────────────

    def test_l2_qwerty_dropped(self, base_ts):
        rows = [
            self._row(base_ts, "qwerty", event_type="keyboard_chunk_capture"),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 0

    def test_l2_normal_text_kept(self, base_ts):
        rows = [
            self._row(base_ts, "design product spec", event_type="keyboard_chunk_capture"),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1

    # ── L4: edit distance within bucket ────────────────────────────────────

    def test_l4_edit_distance_window_dropped(self, base_ts):
        rows = [
            self._row(base_ts, "jx", event_type="keyboard_chunk_capture", row_id=1),
            self._row(base_ts + timedelta(seconds=5), "jix", event_type="keyboard_chunk_capture", row_id=2),
            self._row(base_ts + timedelta(seconds=10), "jixu", event_type="keyboard_chunk_capture", row_id=3),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 0, "Bucket (app, window, ts//300) with 3 similar short edits should drop all"

    def test_l4_long_total_len_kept(self, base_ts):
        rows = [
            self._row(base_ts, "这是一段较长的输入A", event_type="keyboard_chunk_capture", row_id=1),
            self._row(base_ts + timedelta(seconds=5), "这是一段较长的输入B", event_type="keyboard_chunk_capture", row_id=2),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 2, "Total length >= 15, should keep even if distance < 2"

    def test_l4_distinct_samples_kept(self, base_ts):
        rows = [
            self._row(base_ts, "hello world today", event_type="keyboard_chunk_capture", row_id=1),
            self._row(base_ts + timedelta(seconds=5), "goodbye moon night", event_type="keyboard_chunk_capture", row_id=2),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 2, "Large edit distance should keep both"

    # ── Non-keyboard event types ────────────────────────────────────────────

    def test_window_focus_not_filtered(self, base_ts):
        rows = [
            self._row(base_ts, "x", event_type="window_focus"),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1, "window_focus type should not be subject to L1/L2 filters"

    def test_ocr_text_not_filtered(self, base_ts):
        rows = [
            self._row(base_ts, "ab", event_type="ocr_text_capture"),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1, "ocr event type should not be subject to L1/L2 filters"

    # ── Real-world scenario ────────────────────────────────────────────────

    def test_real_25th_noise(self, base_ts):
        """Simulates the 25th report real scenario:
        5x loginwindow noise + 3x jx/jixjixu/xxx keyboard + 1x normal text.
        Expected: only the normal text row survives.
        """
        rows = [
            # 5 loginwindow rows (L3 blocked)
            self._row(base_ts, "pw", window_title="loginwindow", row_id=10),
            self._row(base_ts + timedelta(seconds=1), "pwd123", window_title="loginwindow", row_id=11),
            self._row(base_ts + timedelta(seconds=2), "secret", window_title="loginwindow", row_id=12),
            self._row(base_ts + timedelta(seconds=3), "token", window_title="loginwindow", row_id=13),
            self._row(base_ts + timedelta(seconds=4), "2fa", window_title="loginwindow", row_id=14),
            # 3 short/similar keyboard rows in same bucket (L1+L4 blocked)
            self._row(base_ts + timedelta(seconds=5), "jx", event_type="keyboard_chunk_capture", row_id=20),
            self._row(base_ts + timedelta(seconds=10), "jixjixu", event_type="keyboard_chunk_capture", row_id=21),
            self._row(base_ts + timedelta(seconds=15), "xxx", event_type="keyboard_chunk_capture", row_id=22),
            # 1 normal row (kept)
            self._row(base_ts + timedelta(seconds=20), "design product spec document", event_type="keyboard_chunk_capture", row_id=30),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1, "Should keep only the normal text row"
        assert result[0]["id"] == 30

    # ── Order preservation ─────────────────────────────────────────────────

    def test_order_preserved(self, base_ts):
        rows = [
            self._row(base_ts, "ok", event_type="keyboard_chunk_capture", row_id=1),
            self._row(base_ts + timedelta(seconds=5), "design doc", event_type="keyboard_chunk_capture", row_id=2),
            self._row(base_ts + timedelta(seconds=10), "yes", event_type="keyboard_chunk_capture", row_id=3),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 3
        assert [r["id"] for r in result] == [1, 2, 3]

    # ── Edge cases ─────────────────────────────────────────────────────────

    def test_empty_input(self, base_ts):
        rows = []
        result = filter_noisy_raw_events(rows)
        assert result == []

    def test_all_l3_blocked(self, base_ts):
        rows = [
            self._row(base_ts, "x", window_title="loginwindow", row_id=1),
            self._row(base_ts + timedelta(seconds=1), "y", app_name="1Password", row_id=2),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 0

    def test_mixed_event_types_preserve_non_keyboard(self, base_ts):
        rows = [
            self._row(base_ts, "jx", event_type="keyboard_chunk_capture", row_id=1),
            self._row(base_ts + timedelta(seconds=1), "ab", event_type="window_focus", row_id=2),
            self._row(base_ts + timedelta(seconds=2), "good text here", event_type="keyboard_chunk_capture", row_id=3),
        ]
        result = filter_noisy_raw_events(rows)
        # jx dropped by L1, window_focus kept (non-keyboard), good text kept
        assert len(result) == 2
        assert [r["id"] for r in result] == [2, 3]

    def test_l4_bucket_across_different_windows(self, base_ts):
        """Rows in different windows shouldn't be bucketed together for L4."""
        rows = [
            self._row(base_ts, "jx", window_title="Window A", row_id=1),
            self._row(base_ts + timedelta(seconds=5), "jix", window_title="Window B", row_id=2),
            self._row(base_ts + timedelta(seconds=10), "jixu", window_title="Window C", row_id=3),
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 0, "All dropped by L1 (short, not in whitelist)"

    def test_empty_content_text_not_filtered(self, base_ts):
        rows = [
            self._row(base_ts, "", event_type="keyboard_chunk_capture", row_id=1),
            self._row(base_ts + timedelta(seconds=1), "good", event_type="keyboard_chunk_capture", row_id=2),
        ]
        result = filter_noisy_raw_events(rows)
        # Empty content_text: passes extraction but is empty, so handled gracefully
        assert len(result) >= 1  # At minimum, row 2 is kept

    # ── L3: capture bug content override ────────────────────────────────────

    def test_l3_capture_bug_long_content_kept(self, base_ts):
        """macOS capture bug: app_name wrongly labeled as loginwindow, but clipboard has real content."""
        rows = [{
            "id": 1,
            "event_type": "clipboard_copy",
            "ts_start": base_ts.isoformat(),
            "app_name": "loginwindow",
            "window_title": None,
            "content_text": "(agent产品经理(北京)\n岗位职责:\n1.负责AI硬件产品的软件与智能能力定义",
        }]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1, "Long content (>20 chars) should override app_name=loginwindow"

    def test_l3_app_loginwindow_empty_content_dropped(self, base_ts):
        """If app_name=loginwindow and content is empty or very short, drop it."""
        rows = [{
            "id": 2,
            "event_type": "ax_text_capture",
            "ts_start": base_ts.isoformat(),
            "app_name": "loginwindow",
            "window_title": "",
            "content_text": "",
        }]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 0, "Empty content with app_name=loginwindow should be dropped"

    def test_l3_window_title_login_always_dropped(self, base_ts):
        """window_title is a strong signal; content_text should not override it."""
        rows = [{
            "id": 3,
            "event_type": "clipboard_copy",
            "ts_start": base_ts.isoformat(),
            "app_name": "Chrome",
            "window_title": "登录",
            "content_text": "(agent产品经理(北京)\n岗位职责:\n1.负责AI硬件产品的软件与智能能力定义",
        }]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 0, "window_title with login keyword should always drop, even if content is long"

    # ── L5: pinyin stream filter ────────────────────────────────────────────

    def test_l5_pinyin_stream_dropped(self, base_ts):
        """IME pinyin intermediate state like 'AIchuangyegongsidouyounaxie' should be dropped."""
        rows = [{
            "id": 1,
            "event_type": "keyboard_chunk",
            "ts_start": base_ts.isoformat(),
            "app_name": "Chrome",
            "window_title": "Google",
            "content_text": json.dumps({"text": "AIchuangyegongsidouyounaxie"}),
        }]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 0, "Pinyin stream should be dropped by L5"

    def test_l5_pinyin_with_digit_dropped(self, base_ts):
        """Pinyin with digits (typo buffer) should also be filtered."""
        rows = [{
            "id": 2,
            "event_type": "ax_text",
            "ts_start": base_ts.isoformat(),
            "app_name": "Notes",
            "window_title": None,
            "content_text": "nianbabao241w.wogongzihendid",
        }]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 0, "Pinyin with digits should be dropped by L5"

    def test_l5_short_pinyin_kept(self, base_ts):
        """Pinyin stream under 12 chars (L5 threshold) should be kept."""
        rows = [{
            "id": 3,
            "event_type": "keyboard_chunk",
            "ts_start": base_ts.isoformat(),
            "app_name": "Notes",
            "window_title": None,
            "content_text": "vkaishizuo",
        }]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1, "Short pinyin (< 12 chars) should be kept"

    def test_l5_normal_english_kept(self, base_ts):
        """Normal English code/text should not match pinyin heuristic."""
        rows = [{
            "id": 4,
            "event_type": "keyboard_chunk",
            "ts_start": base_ts.isoformat(),
            "app_name": "Code",
            "window_title": "fragments.py",
            "content_text": "def extract_fragments_from_db",
        }]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1, "Normal English identifier should be kept"

    def test_l5_with_space_kept(self, base_ts):
        """Text with spaces is not a pinyin stream (IME would produce continuous stream)."""
        rows = [{
            "id": 5,
            "event_type": "keyboard_chunk",
            "ts_start": base_ts.isoformat(),
            "app_name": "Notes",
            "window_title": None,
            "content_text": "this is normal english text",
        }]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1, "Text with spaces should be kept"

    def test_l5_multiple_rows_mixed(self, base_ts):
        """Real-world mix: 3 pinyin + 1 good content → only good survives."""
        rows = [
            {
                "id": 1,
                "event_type": "keyboard_chunk",
                "ts_start": base_ts.isoformat(),
                "app_name": "Chrome",
                "window_title": None,
                "content_text": "AIchuangyegongsidouyounaxie",
            },
            {
                "id": 2,
                "event_type": "keyboard_chunk",
                "ts_start": (base_ts + timedelta(seconds=5)).isoformat(),
                "app_name": "Chrome",
                "window_title": None,
                "content_text": "lianwangsousoukannaxnaxie2AIchuangyegongsideshenmegangwei",
            },
            {
                "id": 3,
                "event_type": "clipboard_copy",
                "ts_start": (base_ts + timedelta(seconds=10)).isoformat(),
                "app_name": "Notes",
                "window_title": None,
                "content_text": "nianbabao241w.wogongzihendid",
            },
            {
                "id": 4,
                "event_type": "keyboard_chunk",
                "ts_start": (base_ts + timedelta(seconds=15)).isoformat(),
                "app_name": "Notes",
                "window_title": None,
                "content_text": "产品经理的日常工作",
            },
        ]
        result = filter_noisy_raw_events(rows)
        assert len(result) == 1, "Only real Chinese/meaningful text should survive"
        assert result[0]["id"] == 4

    def test_l5_short_pinyin_now_dropped_under_neutral_rule(self, base_ts):
        """Short pinyin like 'shibeshi2bendicommit' (20 chars) was missed by old
        pattern-based L5; new boundary-based rule catches it."""
        rows = [
            {"id": 1, "event_type": "keyboard_chunk", "ts_start": base_ts.isoformat(),
             "app_name": "Chrome", "window_title": None,
             "content_text": "shibeshi2bendicommit"},
            {"id": 2, "event_type": "keyboard_chunk", "ts_start": base_ts.isoformat(),
             "app_name": "Chrome", "window_title": None,
             "content_text": "yixiarnahouwocompactba"},
        ]
        assert filter_noisy_raw_events(rows) == []

    def test_l5_cjk_text_kept(self, base_ts):
        """Real Chinese prose (CJK chars present) must survive — even without spaces."""
        rows = [{"id": 1, "event_type": "keyboard_chunk", "ts_start": base_ts.isoformat(),
                 "app_name": "Notes", "window_title": None,
                 "content_text": "今天和Haiku讨论了输入卫生过滤器的设计取舍"}]
        assert len(filter_noisy_raw_events(rows)) == 1

    def test_l5_long_english_with_spaces_kept(self, base_ts):
        """Normal English sentence (has spaces) must survive."""
        rows = [{"id": 1, "event_type": "keyboard_chunk", "ts_start": base_ts.isoformat(),
                 "app_name": "Notes", "window_title": None,
                 "content_text": "this is a perfectly reasonable english sentence"}]
        assert len(filter_noisy_raw_events(rows)) == 1

    def test_l5_camelcase_code_kept(self, base_ts):
        """camelCase identifiers (≥3 mixed cases) are real code → keep."""
        rows = [{"id": 1, "event_type": "keyboard_chunk", "ts_start": base_ts.isoformat(),
                 "app_name": "VS Code", "window_title": None,
                 "content_text": "extractFragmentsFromDatabase"}]
        assert len(filter_noisy_raw_events(rows)) == 1

    def test_l5_underscore_code_kept(self, base_ts):
        """snake_case identifiers (have underscore) are real code → keep."""
        rows = [{"id": 1, "event_type": "keyboard_chunk", "ts_start": base_ts.isoformat(),
                 "app_name": "VS Code", "window_title": None,
                 "content_text": "filter_noisy_raw_events"}]
        assert len(filter_noisy_raw_events(rows)) == 1

    def test_l5_token_dropped(self, base_ts):
        """Long tokens / hashes (no boundary) are noise → drop."""
        rows = [{"id": 1, "event_type": "keyboard_chunk", "ts_start": base_ts.isoformat(),
                 "app_name": "Terminal", "window_title": None,
                 "content_text": "cf3a52ee37ad4f8fb0a5707008dfb90c"}]
        assert filter_noisy_raw_events(rows) == []
