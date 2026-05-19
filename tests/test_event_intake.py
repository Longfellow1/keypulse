from __future__ import annotations

from copy import deepcopy

import pytest

from keypulse.pipeline.event_intake import (
    SOURCE_MIN_QUOTA_BASE,
    cap_events_by_source,
    clean_events_semantic,
    collapse_self_repeat,
    compute_source_quotas,
    extract_code_symbols,
    is_app_chrome,
    is_code_snippet,
    is_empty_event,
    is_input_fragment,
    is_loading_title,
    is_machine_output,
    is_menu_chrome,
    prune_code_snippet,
)


def _event(
    *,
    event_id: str = "e1",
    source: str = "window",
    content: str = "normal work text",
    title: str = "KeyPulse - Doc",
    app: str = "Codex",
    ts: str = "2026-05-18T09:00:00+00:00",
    metadata: dict | None = None,
) -> dict:
    return {
        "id": event_id,
        "source": source,
        "speaker": "user",
        "ts_start": ts,
        "app_name": app,
        "window_title": title,
        "content_text": content,
        "metadata_json": metadata or {"entities": {}},
    }


@pytest.mark.parametrize(
    "payload,expected",
    [
        (_event(content="KeyPulse - Doc"), True),
        (_event(content="KeyPulse", title="KeyPulse - Doc"), True),
        (_event(content="Codex", app="Codex"), True),
        (_event(content="work detail line", title="KeyPulse - Doc"), False),
    ],
)
def test_is_app_chrome(payload, expected):
    assert is_app_chrome(payload) is expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("新建标签页", True),
        ("加载中…", True),
        ("Loading", True),
        ("Untitled", True),
        ("...", True),
        ("Project - Chat", False),
    ],
)
def test_is_loading_title(title, expected):
    assert is_loading_title(title) is expected


@pytest.mark.parametrize(
    "content,expected",
    [
        ("[已恢复 2026-05-19]", True),
        ("export https_proxy=http://127.0.0.1:7890", True),
        ("redacted-shell-2026.log", True),
        ("export-foo-bar", True),
        ("data:text/plain;base64,abc", True),
        ("<!DOCTYPE html><html></html>", True),
        ("real user summary", False),
    ],
)
def test_is_machine_output(content, expected):
    assert is_machine_output(content) is expected


@pytest.mark.parametrize(
    "content,expected",
    [
        ("FILE EDIT SAVE", True),
        ("Run>Build>Test>Go", True),
        ("Keep this short note", False),
        ("This is definitely too long for menu chrome", False),
    ],
)
def test_is_menu_chrome(content, expected):
    assert is_menu_chrome(content) is expected


@pytest.mark.parametrize(
    "content,expected",
    [
        ("guoneideguowaide,shifoukaiyuan,renqigkeyizhenghedaoharness", True),
        ("Agent系统", False),
        ("def foo(): return 1", False),
        ("hi how are you", False),
        ("https://x.com/feed", False),
    ],
)
def test_is_input_fragment(content, expected):
    assert is_input_fragment(content) is expected


def test_is_empty_event_detects_blank_payload():
    assert is_empty_event(_event(content="", title="", metadata={"entities": {}})) is True


def test_is_empty_event_keeps_event_with_url():
    event = _event(content="", title="", metadata={"entities": {"urls": ["https://example.com"]}})
    assert is_empty_event(event) is False


def test_is_empty_event_keeps_idle_with_meaningful_metadata():
    event = _event(source="idle", content="", title="", metadata={"entities": {"named_entities": ["KeyPulse"]}})
    assert is_empty_event(event) is False


def test_is_code_snippet_true_for_multiline_code():
    text = "def foo(bar):\n    result_value = helper_call(bar)\n    return result_value\nclass MyHelperClass:\n    pass\n"
    assert is_code_snippet(text) is True


def test_is_code_snippet_false_for_plain_text():
    text = "今天继续推进 daily pipeline，并且复盘了昨天失败的 root cause。"
    assert is_code_snippet(text) is False


def test_extract_code_symbols_picks_first_five_unique():
    text = "def foo(): pass\nclass Bar: pass\nconst baz = 1\nlet qux = 2\nfunction zap() {}\ndef foo(): pass\nvar tail = 3"
    assert extract_code_symbols(text) == ["foo", "Bar", "baz", "qux", "zap"]


def test_extract_code_symbols_respects_max_n():
    assert extract_code_symbols("def alpha(): pass\nclass Beta: pass", max_n=1) == ["alpha"]


def test_prune_code_snippet_uses_120_for_claude_code():
    text = "def worker_task():\n    return 42\n" + ("x" * 300)
    event = _event(source="claude_code", content=text, metadata={"entities": {}})
    pruned = prune_code_snippet(event, "claude_code")
    assert len(pruned["content_text"]) == 120
    assert pruned["metadata"]["code_symbols"] == ["worker_task"]


def test_prune_code_snippet_uses_60_for_other_sources():
    text = "function buildReport() { return true; }\n" + ("x" * 200)
    event = _event(source="window", content=text, metadata={"entities": {}})
    pruned = prune_code_snippet(event, "window")
    assert len(pruned["content_text"]) == 60
    assert pruned["metadata"]["code_symbols"] == ["buildReport"]


def test_clean_events_semantic_applies_filters_and_transforms():
    code_text = (
        "def build_pipeline(config):\n"
        "    report_count = 1\n"
        "    report_name = 'daily'\n"
        "    return report_count\n"
    )
    events = [
        _event(event_id="drop-app", content="KeyPulse - Doc"),
        _event(event_id="drop-machine", content="data:text/plain;base64,AAAA"),
        _event(event_id="drop-menu", content="FILE EDIT SAVE"),
        _event(event_id="drop-input", content="guoneideguowaide,shifoukaiyuan,renqigkeyizhenghedaoharness"),
        _event(event_id="drop-empty", content="", title="", metadata={"entities": {}}),
        _event(event_id="clear-title", content="real note", title="加载中…"),
        _event(event_id="prune-code", source="codex_cli", content=code_text),
    ]
    cleaned = clean_events_semantic(events)
    ids = [item["id"] for item in cleaned]
    assert ids == ["clear-title", "prune-code"]
    assert cleaned[0]["window_title"] == ""
    assert len(cleaned[1]["content_text"]) <= 120
    assert cleaned[1]["metadata"]["code_symbols"] == ["build_pipeline"]


def test_compute_source_quotas_smoke_each_source_has_minimum_five():
    quotas = compute_source_quotas({"s1": 100, "s2": 50, "s3": 30}, limit=60)
    assert all(quotas[source] >= SOURCE_MIN_QUOTA_BASE for source in ("s1", "s2", "s3"))


def test_compute_source_quotas_degrades_min_to_4():
    counts = {f"s{i}": 20 for i in range(1, 14)}
    quotas = compute_source_quotas(counts, limit=60)
    assert all(quotas[source] >= 4 for source in counts)


def test_compute_source_quotas_degrades_min_to_3():
    counts = {f"s{i}": 10 for i in range(1, 17)}
    quotas = compute_source_quotas(counts, limit=60)
    assert all(quotas[source] >= 3 for source in counts)


def test_compute_source_quotas_degrades_to_limit_div_n():
    counts = {f"s{i}": 10 for i in range(1, 26)}
    quotas = compute_source_quotas(counts, limit=60)
    assert all(quotas[source] >= 2 for source in counts)


def test_compute_source_quotas_idle_has_no_base_floor():
    quotas = compute_source_quotas({"idle": 100, "window": 8, "clipboard": 9}, limit=12)
    assert quotas["window"] >= 5
    assert quotas["clipboard"] >= 5
    assert quotas["idle"] >= 0


def test_collapse_self_repeat_merges_within_60s_at_065():
    left = _event(event_id="1", content="alpha beta gamma", ts="2026-05-18T09:00:00+00:00")
    right = _event(event_id="2", content="alpha beta gamma delta", ts="2026-05-18T09:00:30+00:00")
    out = collapse_self_repeat([left, right])
    assert len(out) == 1
    assert out[0]["content_text"] == "alpha beta gamma delta"
    assert out[0]["dwell_seconds"] == 30


def test_collapse_self_repeat_merges_61_300s_at_080():
    left = _event(event_id="1", content="a b c d e", ts="2026-05-18T09:00:00+00:00")
    right = _event(event_id="2", content="a b c d e f", ts="2026-05-18T09:02:00+00:00")
    out = collapse_self_repeat([left, right])
    assert len(out) == 1


def test_collapse_self_repeat_does_not_merge_61_300s_below_threshold():
    left = _event(event_id="1", content="alpha beta gamma delta", ts="2026-05-18T09:00:00+00:00")
    right = _event(event_id="2", content="alpha beta theta omega", ts="2026-05-18T09:03:00+00:00")
    out = collapse_self_repeat([left, right])
    assert len(out) == 2


def test_collapse_self_repeat_does_not_merge_after_300s():
    left = _event(event_id="1", content="alpha beta gamma", ts="2026-05-18T09:00:00+00:00")
    right = _event(event_id="2", content="alpha beta gamma", ts="2026-05-18T09:06:00+00:00")
    out = collapse_self_repeat([left, right])
    assert len(out) == 2


def test_collapse_self_repeat_exempts_different_file_paths():
    left = _event(
        event_id="1",
        content="alpha beta gamma",
        ts="2026-05-18T09:00:00+00:00",
        metadata={"entities": {"file_paths": ["/tmp/a.py"]}},
    )
    right = _event(
        event_id="2",
        content="alpha beta gamma",
        ts="2026-05-18T09:00:30+00:00",
        metadata={"entities": {"file_paths": ["/tmp/b.py"]}},
    )
    out = collapse_self_repeat([left, right])
    assert len(out) == 2


def test_collapse_self_repeat_exempts_different_urls():
    left = _event(
        event_id="1",
        content="alpha beta gamma",
        ts="2026-05-18T09:00:00+00:00",
        metadata={"entities": {"urls": ["https://a.test"]}},
    )
    right = _event(
        event_id="2",
        content="alpha beta gamma",
        ts="2026-05-18T09:00:30+00:00",
        metadata={"entities": {"urls": ["https://b.test"]}},
    )
    out = collapse_self_repeat([left, right])
    assert len(out) == 2


def test_cap_events_by_source_caps_and_retains_source_spread():
    events: list[dict] = []
    for source in ("window", "ax_text", "clipboard"):
        for idx in range(8):
            events.append(
                _event(
                    event_id=f"{source}-{idx}",
                    source=source,
                    content=f"{source} stable content {idx}",
                    title=f"{source} - work {idx}",
                    ts=f"2026-05-18T09:{idx:02d}:00+00:00",
                )
            )

    selected, capped = cap_events_by_source(deepcopy(events), limit=15)
    assert capped is True
    assert len(selected) == 15
    per_source: dict[str, int] = {}
    for item in selected:
        per_source[item["source"]] = per_source.get(item["source"], 0) + 1
    assert per_source["window"] >= 5
    assert per_source["ax_text"] >= 5
    assert per_source["clipboard"] >= 5


def test_cap_events_by_source_cleans_input_fragments_and_code_symbols():
    code_text = (
        "def batchRenderDaily(flag):\n"
        "    helper_value = 1\n"
        "    report_name = 'daily'\n"
        "    return helper_value\n"
    )
    events = [
        _event(event_id="noise", source="window", content="guoneideguowaide,shifoukaiyuan,renqigkeyizhenghedaoharness"),
        _event(event_id="code", source="window", content=code_text, title="KeyPulse - editor"),
    ]
    selected, capped = cap_events_by_source(deepcopy(events), limit=60)
    assert capped is True
    assert [item["id"] for item in selected] == ["code"]
    assert len(selected[0]["content_text"]) <= 60
    assert selected[0]["metadata"]["code_symbols"] == ["batchRenderDaily"]
