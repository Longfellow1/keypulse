"""Experiment (exp/evidence-context): multi-window search snippets and get_evidence focus context.

Synthetic data only. Pure window functions are tested directly; the MCP-facing behaviour (validation,
scope, redaction of `context`) goes through tools.get_evidence / search_context like the other tests."""
from __future__ import annotations

import json
import random

import pytest

from keypulse.mcp import auth, tools
from keypulse.search.engine import SNIPPET_MAX_CHARS, WINDOW_SEP, _snippet
from keypulse.store.db import close, get_conn, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event
from keypulse.store.sync_repo import add_evidence
from mcp_helpers import client_of


def _old_snippet(text: str, terms: list[str], width: int = 80) -> str:
    """The pre-experiment single-window behaviour, kept here as the reference for 0 / 1 match."""
    text = " ".join((text or "").split())
    lowered = text.lower()
    pos = -1
    for term in terms:
        pos = lowered.find(term.lower())
        if pos >= 0:
            break
    if pos < 0:
        return text[: width * 2]
    start = max(0, pos - width)
    end = min(len(text), pos + width + 40)
    return f"{'…' if start > 0 else ''}{text[start:end]}{'…' if end < len(text) else ''}"


def _filler(n: int, seed: str = "lorem ipsum dolor sit amet ") -> str:
    return (seed * (n // len(seed) + 1))[:n]


# ── snippet windows (pure) ───────────────────────────────────────────────────────────────────
def test_single_match_keeps_the_original_window() -> None:
    text = _filler(300) + " needle " + _filler(300)
    assert _snippet(text, ["needle"]) == _old_snippet(text, ["needle"])
    head = "needle at the very start " + _filler(400)
    assert _snippet(head, ["needle"]) == _old_snippet(head, ["needle"])
    tail = _filler(400) + " needle"
    assert _snippet(tail, ["needle"]) == _old_snippet(tail, ["needle"])


def test_zero_match_falls_back_to_the_head() -> None:
    text = _filler(500)
    assert _snippet(text, ["zzqq"]) == _old_snippet(text, ["zzqq"]) == text[:160]


def test_multiple_terms_get_one_window_each_in_text_order() -> None:
    text = _filler(120) + " alpha " + _filler(600) + " beta " + _filler(600) + " gamma " + _filler(120)
    out = _snippet(text, ["gamma", "beta", "alpha"])  # query order must not decide the layout
    assert out.count(WINDOW_SEP) == 2
    a, b, g = out.index("alpha"), out.index("beta"), out.index("gamma")
    assert a < b < g
    assert len(out) <= SNIPPET_MAX_CHARS


def test_overlapping_windows_merge_into_one() -> None:
    text = _filler(200) + " alpha and beta sit close together " + _filler(200)
    out = _snippet(text, ["alpha", "beta"])
    assert WINDOW_SEP not in out
    assert "alpha" in out and "beta" in out


def test_at_most_three_windows_taken_in_text_order() -> None:
    parts = []
    for name in ("t1word", "t2word", "t3word", "t4word", "t5word"):
        parts.append(_filler(500) + f" {name} ")
    text = "".join(parts) + _filler(300)
    out = _snippet(text, ["t5word", "t4word", "t3word", "t2word", "t1word"])
    assert out.count(WINDOW_SEP) <= 2
    assert "t1word" in out and "t2word" in out and "t3word" in out
    assert "t4word" not in out and "t5word" not in out
    assert len(out) <= SNIPPET_MAX_CHARS


def test_duplicate_keywords_differing_only_in_case_count_once() -> None:
    text = _filler(200) + " Redis queue " + _filler(200)
    assert _snippet(text, ["Redis", "redis"]) == _old_snippet(text, ["Redis", "redis"])


def test_total_snippet_never_exceeds_the_cap_on_random_input() -> None:
    rng = random.Random(7)
    words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
    for _ in range(300):
        pieces = [rng.choice(words) if rng.random() < 0.02 else _filler(rng.randint(1, 400), "xy ") for _ in range(12)]
        text = " ".join(pieces)
        out = _snippet(text, rng.sample(words, rng.randint(1, 6)))
        assert len(out) <= SNIPPET_MAX_CHARS


def test_chinese_text_windows_and_cap() -> None:
    text = "前面很长的一段生活记录" * 30 + " 签证材料清单在这里 " + "无关内容" * 80 + " 澳洲 " + "后面的文字" * 40
    out = _snippet(text, ["签证", "澳洲"])
    assert "签证" in out and "澳洲" in out and WINDOW_SEP in out
    assert len(out) <= SNIPPET_MAX_CHARS
    single = _snippet(text, ["签证"])
    assert single == _old_snippet(text, ["签证"])


# ── focus windows (pure) ─────────────────────────────────────────────────────────────────────
def test_focus_without_a_match_is_empty() -> None:
    assert tools.focus_context(_filler(900), ["zzqq"]) == ""


def test_focus_matches_case_insensitively_and_normalises_spaces() -> None:
    text = _filler(700) + " The Quux   Answer " + _filler(700)
    out = tools.focus_context(text, ["quux answer"])
    assert "Quux   Answer" not in out  # text is whitespace-normalised first, as excerpts are
    assert "The Quux Answer" in out


def test_focus_windows_merge_and_stay_about_300_chars() -> None:
    text = _filler(500) + " target " + _filler(60) + " target " + _filler(500)
    out = tools.focus_context(text, ["target"])
    assert out.count("target") == 2 and WINDOW_SEP not in out  # one merged window, no separator
    assert len(out) <= 368 + 2  # span from first window start to second window end, plus two ellipses


def test_focus_cap_is_1500_chars_and_6_windows() -> None:
    text = " ".join(f"hit{i} " + _filler(400) for i in range(40))
    out = tools.focus_context(text, ["hit"])
    assert len(out) <= tools.FOCUS_MAX_CHARS
    assert len(out.split(WINDOW_SEP)) <= tools.FOCUS_MAX_WINDOWS
    assert "hit0" in out and "hit39" not in out  # text order: the start of the record wins


def test_focus_windows_keep_text_order() -> None:
    text = _filler(300) + " first-term " + _filler(900) + " second-term " + _filler(300)
    out = tools.focus_context(text, ["second-term", "first-term"])
    assert out.index("first-term") < out.index("second-term")


# ── MCP level (db) ───────────────────────────────────────────────────────────────────────────
@pytest.fixture
def db(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    init_db(tmp_path / "t.db")
    yield tmp_path
    close()
    tools.configure(mask_emails=True)


def _row(db, text, n, bucket="Work"):
    f = db / f"ctx{n}.jsonl"
    f.write_text(text + "\n")
    rid = insert_raw_event(RawEvent(source="codex_cli", event_type="semantic_event", ts_start="2026-03-01T09:00:00+00:00",
                                    content_text=text, content_hash=f"ctx{n}", bucket=bucket))
    add_evidence(rid, str(f), 1, text)
    return get_conn().execute("SELECT id FROM evidence WHERE event_id = ?", (rid,)).fetchone()[0]


def _long_text(answer: str) -> str:
    return _filler(900) + f" The answer is {answer}. " + _filler(900)


def test_without_focus_the_response_has_no_context_key(db) -> None:
    eid = _row(db, _long_text("zebraquux"), 1)
    token = auth.create_grant("host", buckets=["Work"])
    ev = tools.get_evidence(token, f"evd_{eid}", client=client_of(token))
    assert ev["status"] == "ok" and "context" not in ev["evidence"]


def test_focus_adds_context_the_excerpt_cannot_show(db) -> None:
    eid = _row(db, _long_text("zebraquux"), 2)
    token = auth.create_grant("host", buckets=["Work"])
    ev = tools.get_evidence(token, f"evd_{eid}", ["zebraquux"], client=client_of(token))
    assert ev["status"] == "ok"
    assert "zebraquux" not in ev["evidence"]["excerpt"]  # beyond the 400-char excerpt
    assert "zebraquux" in ev["evidence"]["context"]
    assert len(ev["evidence"]["context"]) <= tools.FOCUS_MAX_CHARS


def test_focus_with_no_occurrence_returns_empty_context(db) -> None:
    eid = _row(db, _long_text("zebraquux"), 3)
    token = auth.create_grant("host", buckets=["Work"])
    ev = tools.get_evidence(token, f"evd_{eid}", ["nosuchterm"], client=client_of(token))
    assert ev["status"] == "ok" and ev["evidence"]["context"] == ""


@pytest.mark.parametrize("bad", [
    "zebra", [], ["a"] * 7, [1], [""], ["   "], ["x" * 101], {"zebra": 1}, 5, [None],
])
def test_invalid_focus_gives_fixed_message_without_echo(db, bad) -> None:
    eid = _row(db, _long_text("zebraquux"), 4)
    token = auth.create_grant("host", buckets=["Work"])
    ev = tools.get_evidence(token, f"evd_{eid}", bad, client=client_of(token))
    assert ev == {"status": "invalid_argument", "message": tools.FOCUS_MESSAGE}  # fixed text, nothing echoed


def test_focus_context_is_redacted_for_email_and_secret(db) -> None:
    # A row that predates the write-time rules: raw email and an API-key shape sit in the stored text.
    fake_key = "sk-ant-api03-" + "A" * 40
    text = _filler(600) + f" deploy mail ops.person@example.com key {fake_key} done " + _filler(600)
    eid = _row(db, "placeholder", 5)
    get_conn().execute("UPDATE raw_events SET content_text = ? WHERE id = (SELECT event_id FROM evidence WHERE id = ?)",
                       (text, eid))
    get_conn().commit()
    token = auth.create_grant("host", buckets=["Work"])
    ev = tools.get_evidence(token, f"evd_{eid}", ["deploy"], client=client_of(token))
    ctx = ev["evidence"]["context"]
    assert "deploy" in ctx
    assert "ops.person" not in ctx and fake_key not in ctx
    assert "***@example.com" in ctx and "[REDACTED]" in ctx


def test_out_of_scope_with_focus_is_the_same_not_found(db) -> None:
    mine = _row(db, _long_text("zebraquux"), 6, bucket="Work")
    theirs = _row(db, _long_text("zebraquux"), 7, bucket="Personal")
    token = auth.create_grant("host", buckets=["Work"])
    client = client_of(token)
    ghost = tools.get_evidence(token, "evd_424242", ["zebraquux"], client=client)
    assert ghost["status"] == "not_found"
    assert tools.get_evidence(token, f"evd_{theirs}", ["zebraquux"], client=client) == ghost
    assert tools.get_evidence(token, "evd_nope", ["zebraquux"], client=client) == ghost
    assert tools.get_evidence(token, f"evd_{mine}", ["zebraquux"], client=client)["status"] == "ok"


def test_search_snippet_shows_both_keywords_end_to_end(db) -> None:
    text = _filler(700) + " alphaword in the middle " + _filler(700) + " betaword near the end " + _filler(200)
    _row(db, text, 8)
    token = auth.create_grant("host", buckets=["Work"])
    res = tools.search_context(token, ["alphaword", "betaword"], client=client_of(token))
    assert res["status"] == "ok"
    snippet = res["results"][0]["snippet"]
    assert "alphaword" in snippet and "betaword" in snippet and WINDOW_SEP in snippet
    assert len(snippet) <= SNIPPET_MAX_CHARS
