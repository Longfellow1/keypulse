"""Email masking: local part masked, domain kept (alice@example.com -> ***@example.com), at write time
(desensitizer) and at query time (MCP second pass). Synthetic addresses only."""
from __future__ import annotations

import json

import pytest

from keypulse.mcp import auth, tools
from keypulse.privacy.desensitizer import desensitize, desensitize_json_value, mask_emails
from keypulse.store import feedback
from keypulse.store.db import close, init_db
from keypulse.store.models import RawEvent
from keypulse.store.repository import insert_raw_event
from keypulse.store.sync_repo import add_evidence
from mcp_helpers import client_of


# ── desensitizer ─────────────────────────────────────────────────────────────────────────────
def test_local_part_masked_domain_kept() -> None:
    assert desensitize("mail alice.w@example.com now") == "mail ***@example.com now"


def test_multiple_emails_in_one_string() -> None:
    out = desensitize("a@one.example.com, b+tag@two.example.org; c_d@three.example.net")
    assert out == "***@one.example.com, ***@two.example.org; ***@three.example.net"


def test_email_inside_urls_keeps_the_rest() -> None:
    assert desensitize("mailto:someone@example.com?subject=hi") == "mailto:***@example.com?subject=hi"
    assert desensitize("https://x.example.com/u?email=someone@example.com&p=1") == \
        "https://x.example.com/u?email=***@example.com&p=1"


def test_already_masked_is_stable_and_idempotent() -> None:
    once = desensitize("someone@example.com")
    assert once == "***@example.com"
    assert desensitize(once) == once
    assert mask_emails(once) == once
    assert mask_emails(mask_emails("x@example.com y@example.com")) == "***@example.com ***@example.com"


def test_ssh_remote_and_shell_prompt_are_not_mangled() -> None:
    # The service account is not a person: keep clone URLs usable.
    assert desensitize("origin git@github.com:org/repo.git (fetch)") == "origin git@github.com:org/repo.git (fetch)"
    # No TLD -> not an email.
    assert desensitize("deploy@buildhost % ls") == "deploy@buildhost % ls"
    # A real person on the same host is still masked.
    assert desensitize("alice@github.com") == "***@github.com"


def test_redact_emails_false_leaves_email() -> None:
    assert desensitize("someone@example.com", redact_emails=False) == "someone@example.com"


def test_json_value_masks_nested() -> None:
    assert desensitize_json_value({"e": ["someone@example.com"]}) == {"e": ["***@example.com"]}


def test_feedback_text_uses_same_mask() -> None:
    assert feedback.clean_text("ask someone@example.com about it", 200) == "ask ***@example.com about it"


# ── MCP query time ───────────────────────────────────────────────────────────────────────────
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


def _row(db, source, text, n):
    f = db / f"{source}.jsonl"
    f.write_text(text + "\n")
    # insert_raw_event only strips secrets, never emails: this is a row an importer wrote unmasked.
    rid = insert_raw_event(RawEvent(source=source, event_type="semantic_event", ts_start="2026-03-01T09:00:00+00:00",
                                    content_text=text, content_hash=f"mask{n}", bucket="Work"))
    add_evidence(rid, str(f), 1, text)
    return rid


@pytest.mark.parametrize("source", ["codex_cli", "claude_code"])
def test_mcp_masks_unmasked_stored_email_in_all_three_tools(db, source) -> None:
    _row(db, source, "ping someone@example.com about zebrafish", 1)
    token = auth.create_grant("host", buckets=["Work"])
    client = client_of(token)

    found = tools.search_context(token, ["zebrafish"], client=client)
    assert found["status"] == "ok"
    evd = found["results"][0]["evidence_id"]
    line = tools.get_timeline(token, "2026-03-01", "2026-03-02", client=client)
    ev = tools.get_evidence(token, evd, client=client)
    assert ev["status"] == "ok"

    for payload in (found, line, ev):
        blob = json.dumps(payload, ensure_ascii=False)
        assert "someone" not in blob
        assert "***@example.com" in blob
    assert json.dumps(ev).count(f"{source}.jsonl") >= 1  # traceability = location, not the address


def test_query_time_mask_follows_config_flag(db) -> None:
    _row(db, "codex_cli", "ping someone@example.com about zebrafish", 2)
    token = auth.create_grant("host", buckets=["Work"])
    tools.configure(mask_emails=False)
    assert "someone@example.com" in json.dumps(tools.search_context(token, ["zebrafish"], client=client_of(token)))
    tools.configure(mask_emails=True)
    assert "someone" not in json.dumps(tools.search_context(token, ["zebrafish"], client=client_of(token)))
