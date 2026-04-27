import pytest
from keypulse.pipeline.normalize import (
    canonicalize_app,
    strip_slug_tail,
    title_to_object_hint,
)


class TestCanonicalizeApp:
    def test_none_returns_empty_string(self):
        assert canonicalize_app(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert canonicalize_app("") == ""

    def test_alias_google_chrome_to_chrome(self):
        assert canonicalize_app("Google Chrome") == "Chrome"

    def test_remove_app_suffix_then_alias(self):
        assert canonicalize_app("Code - Insiders.app") == "VS Code Insiders"

    def test_com_apple_safari_to_safari(self):
        assert canonicalize_app("com.apple.Safari") == "Safari"

    def test_com_apple_terminal_to_terminal(self):
        assert canonicalize_app("com.apple.Terminal") == "Terminal"

    def test_obsidian_app_to_obsidian(self):
        assert canonicalize_app("Obsidian.app") == "Obsidian"

    def test_iterm2_to_iterm(self):
        assert canonicalize_app("iTerm2") == "iTerm"

    def test_unknown_app_name_unchanged(self):
        assert canonicalize_app("UnknownApp") == "UnknownApp"


class TestStripSlugTail:
    def test_none_returns_empty_string(self):
        assert strip_slug_tail(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert strip_slug_tail("") == ""

    def test_browser_tail_with_har_username(self):
        result = strip_slug_tail("AGX_淘宝搜索 - Google Chrome - lang (Har)")
        assert result == "AGX_淘宝搜索"

    def test_em_dash_obsidian_tail(self):
        result = strip_slug_tail("5. 导入前注意 — Obsidian")
        assert result == "5. 导入前注意"

    def test_multiple_tail_iterations(self):
        result = strip_slug_tail("README.md — keypulse - More Info")
        assert result == "README.md"

    def test_no_tail_returns_unchanged(self):
        result = strip_slug_tail("Simple Title")
        assert result == "Simple Title"

    def test_parenthesis_username_tail(self):
        result = strip_slug_tail("Document Title (Harland)")
        assert result == "Document Title"


class TestTitleToObjectHint:
    def test_none_returns_empty_string(self):
        assert title_to_object_hint(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert title_to_object_hint("") == ""

    def test_markdown_file_detected(self):
        result = title_to_object_hint("README.md - Some App (User)")
        assert "文件" in result and "README.md" in result

    def test_python_file_detected(self):
        result = title_to_object_hint("app.py - VS Code (User)")
        assert "文件" in result and "app.py" in result

    def test_https_url_detected(self):
        result = title_to_object_hint("https://github.com/user/repo - Chrome (Har)")
        assert "URL" in result and "github.com" in result

    def test_http_url_detected(self):
        result = title_to_object_hint("http://localhost:8080 - Safari")
        assert "URL" in result

    def test_localhost_detected(self):
        result = title_to_object_hint("localhost:3000 - Chrome (User)")
        assert "URL" in result and "localhost" in result

    def test_chinese_phrase_taobao(self):
        result = title_to_object_hint("AGX_淘宝搜索 - Chrome (Har)")
        assert "淘宝搜索" in result

    def test_chinese_phrase_gouchemen(self):
        result = title_to_object_hint("4、内部购车 - Excel (User)")
        assert "购车" in result

    def test_short_title_unchanged(self):
        result = title_to_object_hint("5. 导入前注意 - Obsidian")
        assert result == "5. 导入前注意"

    def test_long_title_truncated_to_30_chars(self):
        long_title = "This is a very long title that exceeds thirty characters"
        result = title_to_object_hint(long_title)
        assert len(result) <= 30


class TestEndToEndFixtures:
    def test_taobao_search_with_browser_tail(self):
        title = "AGX_淘宝搜索 - Google Chrome - lang (Har)"
        stripped = strip_slug_tail(title)
        assert stripped == "AGX_淘宝搜索"

    def test_gouchemen_with_object_hint(self):
        title = "4、内部购车"
        hint = title_to_object_hint(title)
        assert "购车" in hint

    def test_import_instruction_with_object_hint(self):
        title = "5. 导入前注意"
        hint = title_to_object_hint(title)
        assert hint == "5. 导入前注意"
