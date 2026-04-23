"""Tests for S009RejectCommandAndTitleSlug."""

from __future__ import annotations

import pytest

from keypulse.quality.strategies.cluster.s009_reject_command_and_title_slug import (
    S009RejectCommandAndTitleSlug,
)

_s = S009RejectCommandAndTitleSlug()


# ---------------------------------------------------------------------------
# Regression: today's garbage slugs must all be rejected
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slug",
    [
        # window-title with multi-digit segments
        "macs-fan-control-1-5-20-free-macbookpro21-2",
        # shell command prefix
        "cd-go-corpusflow",
        # git command prefix
        "git-push-u-origin-main",
        # CJK leading + git action suffix
        "先删除现有的-origin",
    ],
)
def test_s009_rejects_garbage_slugs(slug: str):
    verdict = _s.apply(slug)
    assert verdict.accept is False, f"expected reject for {slug!r}"
    assert verdict.reason == "command-title-slug"


# ---------------------------------------------------------------------------
# Command prefix pattern (≥2 positive cases per sub-pattern)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slug",
    [
        "git-commit-m-fix-bug",
        "git-rebase-main",
        "cd-home-projects",
        "ls-la",
        "brew-install-ffmpeg",
        "pip-install-requests",
        "docker-compose-up-d",
        "kubectl-get-pods",
        "npm-run-build",
        "pytest-v-tests",
        "make-clean",
        "go-build",
        "uv-sync",
        "pip3-install-httpx",
    ],
)
def test_s009_rejects_command_prefix_slugs(slug: str):
    verdict = _s.apply(slug)
    assert verdict.accept is False, f"expected reject for {slug!r}"


# ---------------------------------------------------------------------------
# Extra path suffix pattern
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slug",
    [
        "some-config-toml",
        "deploy-sh",
        "output-log",
        "settings-yml",
        "data-json",
        "readme-txt",
        "ci-yaml",
    ],
)
def test_s009_rejects_path_suffix_slugs(slug: str):
    verdict = _s.apply(slug)
    assert verdict.accept is False, f"expected reject for {slug!r}"


# ---------------------------------------------------------------------------
# Extra path prefix pattern
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slug",
    [
        "home-projects-keypulse",
        "home-documents-notes",
    ],
)
def test_s009_rejects_home_prefix_slugs(slug: str):
    verdict = _s.apply(slug)
    assert verdict.accept is False, f"expected reject for {slug!r}"


# ---------------------------------------------------------------------------
# Multi-digit segment pattern (≥2 positive cases)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slug",
    [
        "macs-fan-control-1-5-20-free-macbookpro21-2",  # regression
        "app-2-3-1-release",
        "video-1080-60-10-encode",
    ],
)
def test_s009_rejects_multi_digit_slugs(slug: str):
    verdict = _s.apply(slug)
    assert verdict.accept is False, f"expected reject for {slug!r}"


# ---------------------------------------------------------------------------
# CJK-leading + git action suffix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slug",
    [
        "先删除现有的-origin",   # regression
        "切换到新分支-checkout",
    ],
)
def test_s009_rejects_cjk_lead_cmd_suffix_slugs(slug: str):
    verdict = _s.apply(slug)
    assert verdict.accept is False, f"expected reject for {slug!r}"


# ---------------------------------------------------------------------------
# Negative samples: must all be KEPT
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slug",
    [
        "ai-agent高级产品经理",
        "项目进展与现实差距-claude",
        "keypulse-笔友",
        "corpusflow-数据工程",
        # additional benign slugs
        "obsidian-daily-workflow",
        "product-roadmap-q2",
        "机器学习模型优化",
        "claude-api-集成方案",
    ],
)
def test_s009_keeps_benign_slugs(slug: str):
    verdict = _s.apply(slug)
    assert verdict.accept is True, f"expected keep for {slug!r}"
