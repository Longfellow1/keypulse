from __future__ import annotations

from keypulse.pipeline.surface import build_surface_snapshot
from keypulse.pipeline.surface import translate_why_selected


def test_build_surface_snapshot_filters_noise_and_keeps_explicit_inputs():
    snapshot = build_surface_snapshot(
        [
            {
                "source": "idle",
                "event_type": "idle_start",
                "ts_start": "2026-04-19T09:00:00+00:00",
            },
            {
                "source": "window",
                "event_type": "window_focus",
                "ts_start": "2026-04-19T09:01:00+00:00",
                "app_name": "终端",
                "window_title": "终端",
            },
            {
                "source": "manual",
                "event_type": "manual_save",
                "ts_start": "2026-04-19T09:02:00+00:00",
                "content_text": "决定把高价值判断前移，先过滤噪音，再做候选排序。",
                "topic_key": "knowledge-surface",
                "tags": "product,decision",
            },
            {
                "source": "clipboard",
                "event_type": "clipboard_copy",
                "ts_start": "2026-04-19T09:03:00+00:00",
                "content_text": "方法论：候选打分只保留显式度、决策信号、复用性。",
                "tags": "method",
            },
        ]
    )

    assert snapshot["filtered_total"] == 2
    assert snapshot["filtered_reasons"] == {"idle_event": 1, "low_signal_window": 1}
    assert [item["source"] for item in snapshot["candidates"]] == ["manual", "clipboard"]


def test_build_surface_snapshot_sorts_candidates_and_builds_theme_candidates():
    snapshot = build_surface_snapshot(
        [
            {
                "source": "manual",
                "event_type": "manual_save",
                "content_text": "决定保留高显式度输入，并把主题候选放到首页工作台。",
                "topic_key": "surface",
                "tags": "product,decision",
            },
            {
                "source": "clipboard",
                "event_type": "clipboard_copy",
                "content_text": "另一段不同的文本内容用于测试。",
                "topic_key": "product",
                "tags": "product",
            },
            {
                "source": "window",
                "event_type": "window_focus",
                "app_name": "Cursor",
                "window_title": "KeyPulse design",
                "content_text": "KeyPulse design",
                "tags": "product",
            },
        ],
        top_k=2,
    )

    assert len(snapshot["candidates"]) == 2
    assert snapshot["candidates"][0]["source"] == "manual"
    assert snapshot["candidates"][0]["score"] >= snapshot["candidates"][1]["score"]

    assert snapshot["theme_candidates"][0]["key"] == "topic:surface"
    assert snapshot["theme_candidates"][0]["item_count"] == 1
    assert "高显式度输入" in snapshot["theme_candidates"][0]["top_evidence"]


def test_build_surface_snapshot_deduplicates_content_hash_for_explicit_sources_only():
    cases = [
        ([{"source": "clipboard", "event_type": "clipboard_copy", "content_text": "同一段文本", "content_hash": "dup-clipboard"}] * 2, ["clipboard"]),
        ([{"source": "manual", "event_type": "manual_save", "content_text": "同一段文本", "content_hash": "dup-manual"}] * 2, ["manual"]),
        ([{"source": "window", "event_type": "window_focus", "app_name": "Editor", "window_title": "Long enough title A", "content_text": "This window event is intentionally long enough to stay visible.", "content_hash": "dup-window"}, {"source": "window", "event_type": "window_focus", "app_name": "Editor", "window_title": "Long enough title B", "content_text": "This window event is intentionally long enough to stay visible.", "content_hash": "dup-window"}], ["window", "window"]),
        ([{"source": "clipboard", "event_type": "clipboard_copy", "content_text": "无 hash 事件 A", "content_hash": None}] * 2, ["clipboard", "clipboard"]),
    ]
    for events, expected in cases:
        assert [item["source"] for item in build_surface_snapshot(events)["candidates"]] == expected


def test_translate_why_selected_limits_labels_to_human_facing_signals():
    labels = translate_why_selected(
        {
            "explicitness": 1.0,
            "novelty": 0.1,
            "reusability": 0.1,
            "decision_signal": 0.6,
            "density": 0.2,
            "recurrence": 0.55,
        },
        {"recurrence_count": 3},
    )

    assert labels == ["你主动保存", "最近第 3 次提及"]
