from __future__ import annotations

import pytest

from keypulse.quality import StrategyRegistry
from keypulse.quality.strategies.cluster import register_cluster_strategies
from keypulse.quality.strategies.cluster.s001_reject_empty import S001RejectEmpty
from keypulse.quality.strategies.cluster.s002_reject_numeric_or_separator import (
    S002RejectNumericOrSeparator,
)
from keypulse.quality.strategies.cluster.s003_reject_url_like import S003RejectUrlLike
from keypulse.quality.strategies.cluster.s004_reject_file_path import S004RejectFilePath
from keypulse.quality.strategies.cluster.s005_reject_secret_pattern import (
    S005RejectSecretPattern,
)
from keypulse.quality.strategies.cluster.s006_reject_command_flag import S006RejectCommandFlag
from keypulse.quality.strategies.cluster.s007_reject_terminal_resolution import (
    S007RejectTerminalResolution,
)
from keypulse.quality.strategies.cluster.s008_reject_insufficient_content import (
    S008RejectInsufficientContent,
)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("hello world", "empty"),
        ("中文", "empty"),
    ],
)
def test_s001_reject_empty_accepts_non_empty_values(value: str, reason: str):
    assert S001RejectEmpty().apply(value).accept is True


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
    ],
)
def test_s001_reject_empty_rejects_empty_values(value: str, reason: str):
    verdict = S001RejectEmpty().apply(value)
    assert verdict.accept is False
    assert verdict.reason == reason


@pytest.mark.parametrize(
    "value",
    [
        "alpha",
        "中文主题",
    ],
)
def test_s002_reject_numeric_or_separator_accepts_text(value: str):
    assert S002RejectNumericOrSeparator().apply(value).accept is True


@pytest.mark.parametrize(
    "value",
    [
        "12345",
        "123-._",
    ],
)
def test_s002_reject_numeric_or_separator_rejects_numeric_or_separator_values(value: str):
    verdict = S002RejectNumericOrSeparator().apply(value)
    assert verdict.accept is False
    assert verdict.reason == "numeric-or-separator"


@pytest.mark.parametrize(
    "value",
    [
        "release notes",
        "分析 数据 导出",
    ],
)
def test_s003_reject_url_like_accepts_non_urls(value: str):
    assert S003RejectUrlLike().apply(value).accept is True


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "https-example",
    ],
)
def test_s003_reject_url_like_rejects_url_like_values(value: str):
    verdict = S003RejectUrlLike().apply(value)
    assert verdict.accept is False
    assert verdict.reason == "url-like"


@pytest.mark.parametrize(
    "value",
    [
        "release planning notes",
        "中文主题",
    ],
)
def test_s004_reject_file_path_accepts_non_paths(value: str):
    assert S004RejectFilePath().apply(value).accept is True


@pytest.mark.parametrize(
    "value",
    [
        "/Users/harland/notes/todo.md",
        "library-cache",
    ],
)
def test_s004_reject_file_path_rejects_path_like_values(value: str):
    verdict = S004RejectFilePath().apply(value)
    assert verdict.accept is False
    assert verdict.reason == "file-path"


@pytest.mark.parametrize(
    "value",
    [
        "release planning notes",
        "中文主题",
    ],
)
def test_s005_reject_secret_pattern_accepts_non_secrets(value: str):
    assert S005RejectSecretPattern().apply(value).accept is True


@pytest.mark.parametrize(
    "value",
    [
        "sk-1234567890abcdef",
        "deadbeefdeadbeef",
    ],
)
def test_s005_reject_secret_pattern_rejects_secret_like_values(value: str):
    verdict = S005RejectSecretPattern().apply(value)
    assert verdict.accept is False
    assert verdict.reason == "secret-pattern"


@pytest.mark.parametrize(
    "value",
    [
        "release planning notes",
        "中文主题",
    ],
)
def test_s006_reject_command_flag_accepts_text_without_flags(value: str):
    assert S006RejectCommandFlag().apply(value).accept is True


@pytest.mark.parametrize(
    "value",
    [
        "foo --bar",
        "--help",
    ],
)
def test_s006_reject_command_flag_rejects_flag_like_values(value: str):
    verdict = S006RejectCommandFlag().apply(value)
    assert verdict.accept is False
    assert verdict.reason == "command-flag"


@pytest.mark.parametrize(
    "value",
    [
        "release planning notes",
        "中文主题",
    ],
)
def test_s007_reject_terminal_resolution_accepts_non_resolution_values(value: str):
    assert S007RejectTerminalResolution().apply(value).accept is True


@pytest.mark.parametrize(
    "value",
    [
        "-1920-1080",
        "x-1-2",
    ],
)
def test_s007_reject_terminal_resolution_rejects_resolution_like_values(value: str):
    verdict = S007RejectTerminalResolution().apply(value)
    assert verdict.accept is False
    assert verdict.reason == "terminal-resolution"


@pytest.mark.parametrize(
    "value",
    [
        "中文主题",
        "release planning notes",
    ],
)
def test_s008_reject_insufficient_content_accepts_substantive_values(value: str):
    assert S008RejectInsufficientContent().apply(value).accept is True


@pytest.mark.parametrize(
    "value",
    [
        "A B",
        "单",
    ],
)
def test_s008_reject_insufficient_content_rejects_sparse_values(value: str):
    verdict = S008RejectInsufficientContent().apply(value)
    assert verdict.accept is False
    assert verdict.reason == "insufficient-content"


def test_register_cluster_strategies_registers_in_order():
    registry = StrategyRegistry()

    register_cluster_strategies(registry)

    assert [strategy.id for strategy in registry.all()] == [
        "S001",
        "S002",
        "S003",
        "S004",
        "S005",
        "S006",
        "S007",
        "S008",
        "S009",
    ]
