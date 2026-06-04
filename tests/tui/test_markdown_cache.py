"""Unit tests for ``src.tui.markdown_cache``."""

from __future__ import annotations

import pytest
from rich.markdown import Markdown
from rich.text import Text

from src.tui.markdown_cache import (
    MarkdownCache,
    get_markdown_cache,
    has_markdown_syntax,
    reset_markdown_cache_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_markdown_cache_for_tests()
    yield
    reset_markdown_cache_for_tests()


# ------------------------------------------------------------------
# has_markdown_syntax fast-path
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "hello world",
        "just plain text without anything fancy",
        "a sentence with a number 1234 in it",
        "URL-like fragment example.com without a link",
    ],
)
def test_has_markdown_syntax_returns_false_on_plain_text(text: str) -> None:
    assert has_markdown_syntax(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "# heading",
        "## smaller heading",
        "* a bullet",
        "+ another bullet",
        "- yet another bullet",
        "1. ordered first",
        "> a blockquote",
        "```python\nx = 1\n```",
        "---\nhorizontal rule break",
        "***\nanother HR style",
        "___\nthird HR style",
        "**bold word** in middle",
        "click [a link](https://example.com) here",
        "use `inline code` here",
    ],
)
def test_has_markdown_syntax_detects_each_marker(text: str) -> None:
    assert has_markdown_syntax(text) is True


def test_has_markdown_syntax_only_scans_first_500_chars() -> None:
    # Markdown buried beyond the peek window goes undetected by design.
    text = "plain " * 200 + "\n## buried heading"
    assert len(text) > 500
    assert has_markdown_syntax(text, peek=500) is False


# ------------------------------------------------------------------
# MarkdownCache.get_or_render
# ------------------------------------------------------------------


def test_plain_text_returns_text_renderable() -> None:
    cache = MarkdownCache()
    rendered = cache.get_or_render("just plain content")
    assert isinstance(rendered, Text)


def test_markdown_content_returns_markdown_renderable() -> None:
    cache = MarkdownCache()
    rendered = cache.get_or_render("# heading")
    assert isinstance(rendered, Markdown)


def test_empty_content_returns_empty_text() -> None:
    cache = MarkdownCache()
    rendered = cache.get_or_render("")
    assert isinstance(rendered, Text)
    assert str(rendered) == ""


def test_cache_hit_returns_same_renderable_object() -> None:
    cache = MarkdownCache()
    first = cache.get_or_render("# heading")
    second = cache.get_or_render("# heading")
    assert first is second
    assert cache.hits == 1
    assert cache.misses == 1


def test_cache_keys_distinguish_themes() -> None:
    cache = MarkdownCache()
    light = cache.get_or_render("# h", code_theme="default")
    dark = cache.get_or_render("# h", code_theme="monokai")
    # Different theme → different cache slot → different renderable.
    assert light is not dark
    assert cache.misses == 2


def test_explicit_cache_key_lets_chunks_share_an_entry() -> None:
    """Streaming use case: the caller supplies a stable key for in-flight content."""
    cache = MarkdownCache()
    cache.get_or_render("# heading\n\nfirst chunk so far", cache_key="ck1")
    second = cache.get_or_render("# heading\n\nfirst chunk plus more", cache_key="ck1")
    assert cache.hits == 1
    # The cached renderable was built from the *first* call's content; the
    # second call gets the same object even though its content differs.
    # That's the intended semantics for in-flight chunk sharing — the safe-
    # checkpoint prefix is the unit of cacheability, not every byte.
    assert second is not None


def test_lru_evicts_oldest_when_max_entries_exceeded() -> None:
    cache = MarkdownCache(max_entries=3)
    cache.get_or_render("# a")
    cache.get_or_render("# b")
    cache.get_or_render("# c")
    assert len(cache) == 3
    # Inserting a fourth distinct entry evicts "# a" (the oldest, untouched).
    cache.get_or_render("# d")
    assert len(cache) == 3
    # "# a" is gone; re-rendering it counts as a miss.
    misses_before = cache.misses
    cache.get_or_render("# a")
    assert cache.misses == misses_before + 1


def test_lru_recently_used_entry_is_not_evicted() -> None:
    cache = MarkdownCache(max_entries=3)
    cache.get_or_render("# a")
    cache.get_or_render("# b")
    cache.get_or_render("# c")
    # Touch "# a" so it becomes most recently used.
    cache.get_or_render("# a")
    # Inserting "# d" should evict "# b" (now oldest), not "# a".
    cache.get_or_render("# d")
    misses_before = cache.misses
    cache.get_or_render("# a")
    assert cache.misses == misses_before  # still cached
    cache.get_or_render("# b")
    assert cache.misses == misses_before + 1  # was evicted


def test_clear_resets_state_and_stats() -> None:
    cache = MarkdownCache()
    cache.get_or_render("# heading")
    cache.get_or_render("# heading")
    assert cache.hits == 1
    cache.clear()
    assert len(cache) == 0
    assert cache.hits == 0
    assert cache.misses == 0


def test_render_swallows_partial_token_failures_falls_back_to_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive fallback — if Rich.Markdown errors, we degrade to Text."""

    cache = MarkdownCache()

    def boom(*args, **kwargs):
        raise RuntimeError("simulated Rich.Markdown failure")

    monkeypatch.setattr("src.tui.markdown_cache.Markdown", boom)
    rendered = cache.get_or_render("# heading would normally hit Markdown")
    assert isinstance(rendered, Text)


# ------------------------------------------------------------------
# Singleton accessor
# ------------------------------------------------------------------


def test_singleton_persists_across_get_calls() -> None:
    """The module-level singleton survives "widget unmount" — i.e. callers get the same instance."""

    a = get_markdown_cache()
    b = get_markdown_cache()
    assert a is b


def test_reset_singleton_for_tests_returns_a_fresh_cache() -> None:
    a = get_markdown_cache()
    a.get_or_render("# something")
    reset_markdown_cache_for_tests()
    b = get_markdown_cache()
    assert b is not a
    assert b.misses == 0


def test_default_singleton_has_max_500() -> None:
    cache = get_markdown_cache()
    assert cache.max_entries == 500


def test_constructor_rejects_zero_or_negative_max() -> None:
    with pytest.raises(ValueError):
        MarkdownCache(max_entries=0)
    with pytest.raises(ValueError):
        MarkdownCache(max_entries=-1)
