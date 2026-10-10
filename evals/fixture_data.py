"""Synthetic fixture events (single source of truth for the eval fixture library).

Everything here is invented. Names, URLs, "secrets" and amounts are fake; no real
user data was copied. ``evals.fixtures`` materialises these events into the on-disk
formats that each real source plugin reads (jsonl / history files / sqlite / repos /
markdown vaults), inside a temp directory.

Buckets (D23) are the *intended* classification of each event: Work / Personal /
Private / Financial. The mainline currently only assigns a bucket for claude_code,
codex_cli and git_log (by source default); the others are reported as unbucketed
until #19 lands. The intended bucket is still recorded so coverage can be tracked.

Timestamps are unique per source (the runner resolves fixture ids by (source, time)).
"""
from __future__ import annotations

from dataclasses import dataclass

BUCKETS = ("Work", "Personal", "Private", "Financial")


@dataclass(frozen=True)
class FxEvent:
    id: str
    source: str
    ts: str  # UTC, "YYYY-MM-DDTHH:MM:SS"
    bucket: str
    text: str  # message / commit message / command / page title / note title
    actor: str = "user"  # claude_code only: user | assistant
    group: str = ""  # claude_code: project dir; git: repo name; chrome: profile; markdown: relative dir
    url: str = ""  # browser sources
    expect_ingest: bool = True


# Fake credential: matches the sink redaction pattern, must never be searchable.
FAKE_KEY = "sk-test-FAKE0000000000000000000000"

CC = "claude_code"
CX = "codex_cli"
GL = "git_log"
ZS = "zsh_history"
CH = "chrome_history"
SF = "safari_history"
MD = "markdown_vault"

W, P, V, F = "Work", "Personal", "Private", "Financial"

EVENTS: tuple[FxEvent, ...] = (
    # -- Claude Code ----------------------------------------------------------
    FxEvent("cc-01", CC, "2026-04-20T02:00:00", W, "这个功能的边界我们定了：检索只读，不做写回，写回放到 P1 再说", group="-tmp-proj-memory-core"),
    FxEvent("cc-02", CC, "2026-04-20T02:05:00", W, "确认检索边界：search_context 只读，commit_memory 延后到 P1", "assistant", "-tmp-proj-memory-core"),
    FxEvent("cc-03", CC, "2026-04-21T03:00:00", W, "为什么没用方案 A 的 Redis 队列？因为要本地优先，不想引入常驻服务", group="-tmp-proj-memory-core"),
    FxEvent("cc-04", CC, "2026-04-21T03:04:00", W, "方案 A 的 Redis 队列被否决：需要常驻进程，违背本地优先原则；改用 SQLite WAL 队列", "assistant", "-tmp-proj-memory-core"),
    FxEvent("cc-05", CC, "2026-04-22T01:30:00", W, "昨天和小李聊的那个框架叫 LangGraph，他建议用它做 agent 编排", group="-tmp-proj-memory-core"),
    FxEvent("cc-06", CC, "2026-04-22T01:40:00", W, "Xiao Li suggested we call the vector store layer Harbor internally", group="-tmp-proj-memory-core"),
    FxEvent("cc-07", CC, "2026-04-22T05:00:00", P, "帮我整理澳洲 subclass 600 访客签证需要的材料：护照、资金证明、行程单、邀请函", group="-tmp-proj-life-admin"),
    FxEvent("cc-08", CC, "2026-04-22T05:03:00", P, "签证材料清单：护照扫描件、近三个月银行流水、行程单、体检报告（可选）", "assistant", "-tmp-proj-life-admin"),
    FxEvent("cc-09", CC, "2026-04-23T06:00:00", F, "帮我对比两张信用卡的年费：招行经典白 800 元，交行 Plus 600 元", group="-tmp-proj-life-admin"),
    FxEvent("cc-10", CC, "2026-04-23T14:00:00", V, "我最近在看心理咨询，预约了周四下午三点的咨询师", group="-tmp-proj-life-admin"),
    FxEvent("cc-11", CC, "2026-04-24T02:00:00", W, f"my deploy credential is api_key={FAKE_KEY} please keep the staging rotation notes", group="-tmp-proj-memory-core"),
    FxEvent("cc-12", CC, "2026-04-24T02:30:00", W, "The migration from schema v19 to v20 adds the access_grants and evidence tables", "assistant", "-tmp-proj-memory-core"),
    FxEvent("cc-13", CC, "2026-04-25T01:00:00", W, "排查 launchd 守护进程重启后丢失 cursor 的问题", group="-tmp-proj-memory-core"),
    FxEvent("cc-14", CC, "2026-04-25T01:10:00", W, "根因：cursor 在入库前就提交了；修复方式是入库成功之后才推进 cursor", "assistant", "-tmp-proj-memory-core"),
    FxEvent("cc-15", CC, "2026-04-26T04:00:00", P, "之前买的网球线型号是 Luxilon ALU Power 125，在 Tennis Warehouse 下的单", group="-tmp-proj-life-admin"),
    FxEvent("cc-16", CC, "2026-04-26T08:00:00", W, "发布 deadline 是 5 月 15 日，评审会安排在 5 月 8 日", group="-tmp-proj-memory-core"),
    FxEvent("cc-17", CC, "2026-04-28T08:00:00", W, "更新一下：发布 deadline 改到 5 月 30 日，评审会不变", group="-tmp-proj-memory-core"),
    FxEvent("cc-18", CC, "2026-04-27T02:00:00", W, "评测集要分有答案、无答案、权限受限三类，每个场景至少八条", group="-tmp-proj-memory-core"),
    FxEvent("cc-19", CC, "2026-04-27T03:00:00", W, "Codex 会话是按日期分目录的 rollout 文件，扫描要递归 sessions/YYYY/MM/DD", group="-tmp-proj-memory-core"),
    # -- Codex CLI ------------------------------------------------------------
    FxEvent("cx-01", CX, "2026-04-20T09:00:00", W, "refactor the FTS trigram tokenizer config so short CJK terms fall back to LIKE", group="cx-s1"),
    FxEvent("cx-02", CX, "2026-04-21T09:00:00", W, "写一个脚本批量重命名 Obsidian 笔记的 frontmatter tags", group="cx-s2"),
    FxEvent("cx-03", CX, "2026-04-22T09:00:00", W, "continue yesterday's work on the cursor commit fix in the sources registry", group="cx-s3"),
    FxEvent("cx-04", CX, "2026-04-23T09:00:00", F, "calculate the monthly mortgage payment for a 2.4 million yuan loan at 3.1 percent over 30 years", group="cx-s4"),
    FxEvent("cx-05", CX, "2026-04-24T09:00:00", P, "draft a packing list for the Sydney trip in May including adapters and sunscreen", group="cx-s5"),
    FxEvent("cx-06", CX, "2026-04-25T09:00:00", W, "parse the Codex rollout files recursively under sessions by date and skip developer prompts", group="cx-s6"),
    FxEvent("cx-07", CX, "2026-04-25T09:05:00", W, "rollout parser keeps only user and assistant messages and drops tool call noise", "assistant", "cx-s6"),
    # -- Git log --------------------------------------------------------------
    FxEvent("gl-01", GL, "2026-04-20T10:00:00", W, "feat(mcp): add search_context tool with token auth\n\nread-only retrieval, every hit carries an evidence id", group="memory-core"),
    FxEvent("gl-02", GL, "2026-04-21T10:00:00", W, "fix(sources): advance the cursor only after persist succeeds", group="memory-core"),
    FxEvent("gl-03", GL, "2026-04-22T10:00:00", W, "refactor(store): migrate schema v19 to v20 with access_grants", group="memory-core"),
    FxEvent("gl-04", GL, "2026-04-23T10:00:00", W, "docs: record why the Redis queue was rejected (local-first)", group="memory-core"),
    FxEvent("gl-05", GL, "2026-04-24T10:00:00", W, "chore: bump pyobjc to 11.0 for the macOS extras", group="memory-core"),
    FxEvent("gl-06", GL, "2026-04-25T10:00:00", W, "feat(sources): ingest Codex rollout sessions with a byte-offset resume position", group="memory-core"),
    # -- zsh ------------------------------------------------------------------
    FxEvent("zs-01", ZS, "2026-04-21T11:00:00", W, "sqlite3 demo.db 'PRAGMA wal_checkpoint(TRUNCATE)'"),
    FxEvent("zs-02", ZS, "2026-04-22T11:00:00", W, "launchctl kickstart -k gui/501/com.example.daemon"),
    FxEvent("zs-03", ZS, "2026-04-23T11:00:00", W, "brew install ripgrep"),
    FxEvent("zs-04", ZS, "2026-04-24T11:00:00", W, "docker compose up -d postgres"),
    FxEvent("zs-05", ZS, "2026-04-25T11:00:00", P, "ffmpeg -i interview.mov -vn -acodec mp3 interview.mp3"),
    # -- Chrome ---------------------------------------------------------------
    FxEvent("ch-01", CH, "2026-04-22T04:00:00", P, "Australia Visitor visa (subclass 600) - Home Affairs", group="Default", url="https://immi.example.gov.au/visitor-600"),
    FxEvent("ch-02", CH, "2026-04-22T04:20:00", P, "Visitor visa checklist: financial evidence - Home Affairs", group="Default", url="https://immi.example.gov.au/visitor-600/checklist"),
    FxEvent("ch-03", CH, "2026-04-26T07:00:00", P, "Luxilon ALU Power 125 Tennis String | Tennis Warehouse", group="Default", url="https://shop.example.com/luxilon-alu-power-125"),
    FxEvent("ch-04", CH, "2026-04-20T08:00:00", W, "SQLite FTS5 trigram tokenizer documentation", group="Default", url="https://sqlite.example.org/fts5-trigram"),
    FxEvent("ch-05", CH, "2026-04-23T05:00:00", F, "Credit card annual fee comparison 2026 | CardGuide", group="Default", url="https://cards.example.com/annual-fee-2026"),
    FxEvent("ch-06", CH, "2026-04-21T08:00:00", W, "Model Context Protocol specification - server tools", group="Default", url="https://mcp.example.org/spec/tools"),
    # -- Safari ---------------------------------------------------------------
    FxEvent("sf-01", SF, "2026-04-26T07:30:00", P, "Wirecutter: the best tennis racket strings for spin", url="https://reviews.example.com/tennis-strings"),
    FxEvent("sf-02", SF, "2026-04-23T07:00:00", F, "Mortgage repayment calculator - Example Bank", url="https://bank.example.com/mortgage-calculator"),
    FxEvent("sf-03", SF, "2026-04-23T13:00:00", V, "Therapist directory near me - counselling appointments", url="https://care.example.com/therapists"),
    FxEvent("sf-04", SF, "2026-04-24T07:00:00", P, "Sydney weather in May: averages and what to pack", url="https://weather.example.com/sydney-may"),
    # -- Markdown vault (title only; file mtime = event time) -----------------
    FxEvent("md-01", MD, "2026-04-20T12:00:00", W, "Sprint 1.5 planning: retrieval boundary decisions", group="Projects"),
    FxEvent("md-02", MD, "2026-04-22T12:00:00", P, "Australia trip visa materials checklist", group="Life"),
    FxEvent("md-03", MD, "2026-04-21T12:00:00", W, "Method: agent eval gold set design", group="Projects"),
    FxEvent("md-04", MD, "2026-04-23T12:00:00", F, "2026 household budget and card fees", group="Finance"),
    FxEvent("md-05", MD, "2026-04-23T15:00:00", V, "Journal 2026-04-23 therapy reflections", group="Journal"),
    FxEvent("md-06", MD, "2026-04-26T12:00:00", P, "Tennis string tension log", group="Life"),
)

BY_ID = {e.id: e for e in EVENTS}
assert len(BY_ID) == len(EVENTS), "duplicate fixture ids"
assert len({(e.source, e.ts) for e in EVENTS}) == len(EVENTS), "fixture timestamps must be unique per source"
