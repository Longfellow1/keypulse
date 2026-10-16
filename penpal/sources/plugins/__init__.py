from __future__ import annotations

from penpal.sources.plugins.approved_sqlite import ApprovedSqliteSource
from penpal.sources.plugins.chrome_history import ChromeHistorySource
from penpal.sources.plugins.claude_code import ClaudeCodeSource
from penpal.sources.plugins.codex_cli import CodexCliSource
from penpal.sources.plugins.git_log import GitLogSource
from penpal.sources.plugins.knowledgec import KnowledgeCSource
from penpal.sources.plugins.leveldb_reader import LevelDbReaderSource
from penpal.sources.plugins.markdown_vault import MarkdownVaultSource
from penpal.sources.plugins.safari_history import SafariHistorySource
from penpal.sources.plugins.spotlight import SpotlightSource
from penpal.sources.plugins.wechat import WechatSource
from penpal.sources.plugins.zsh_history import ZshHistorySource


__all__ = [
    "ApprovedSqliteSource",
    "GitLogSource",
    "ClaudeCodeSource",
    "CodexCliSource",
    "ChromeHistorySource",
    "KnowledgeCSource",
    "LevelDbReaderSource",
    "SafariHistorySource",
    "SpotlightSource",
    "ZshHistorySource",
    "MarkdownVaultSource",
    "WechatSource",
]
