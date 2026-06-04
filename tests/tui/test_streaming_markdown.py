"""Tests for progressive Markdown streaming in ``AssistantTextMessage``.

Phase 1 (refactoring plan WI-1.2) lands six safe-checkpoint conditions and a
prefix-hash cache key. These tests exercise:

* The 6 safe checkpoints fire (paragraph, code-fence, heading, list item,
  blockquote, horizontal rule).
* Mid-line / mid-token chunks DO NOT trigger Markdown render — preserving
  the headline failure mode "user sees half-rendered ``**bold`` mid-stream".
* The cache key strategy lets chunks between checkpoints share a cache entry.
* Performance benchmarks (WI-1.3) for typical (~2K token) and worst-case
  (~10K token) streams.
"""

from __future__ import annotations

import os
import time

import pytest
from rich.markdown import Markdown
from rich.text import Text

from src.tui.markdown_cache import (
    MarkdownCache,
    get_markdown_cache,
    reset_markdown_cache_for_tests,
)
from src.tui.widgets.messages.assistant_text import (
    _at_safe_checkpoint,
    _last_checkpoint_prefix,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_markdown_cache_for_tests()
    yield
    reset_markdown_cache_for_tests()


# ------------------------------------------------------------------
# _at_safe_checkpoint — the 6 conditions plus negative cases
# ------------------------------------------------------------------


def test_empty_text_is_not_a_checkpoint() -> None:
    assert _at_safe_checkpoint("") is False


def test_paragraph_break_is_a_checkpoint() -> None:
    assert _at_safe_checkpoint("first para\n\n") is True


def test_single_newline_is_not_a_paragraph_break() -> None:
    assert _at_safe_checkpoint("first line\n") is False


def test_closed_fenced_code_block_is_a_checkpoint() -> None:
    text = "```python\nx = 1\n```\n"
    assert _at_safe_checkpoint(text) is True


def test_open_fenced_code_block_is_NOT_a_checkpoint() -> None:
    """Headline failure mode — open code blocks must NOT trigger render."""
    text = "```python\nx = 1\n"
    assert _at_safe_checkpoint(text) is False


def test_open_fence_after_closed_block_breaks_checkpoint() -> None:
    """Multiple code blocks: a fresh opening fence after a closed pair must NOT render.

    Sequence: closed code block (count=2, even, render OK) followed by an
    opening fence (count=3, odd) — must defer rendering until the new block
    closes. Stops Rich.Markdown from emitting a half-rendered code block.
    """
    one_closed = "```py\nx=1\n```\n"
    assert _at_safe_checkpoint(one_closed) is True
    closed_then_open = one_closed + "more text\n```py\nstill open\n"
    assert _at_safe_checkpoint(closed_then_open) is False


def test_heading_line_is_a_checkpoint() -> None:
    assert _at_safe_checkpoint("# Title\n") is True
    assert _at_safe_checkpoint("###### deepest\n") is True


def test_list_item_line_is_a_checkpoint() -> None:
    assert _at_safe_checkpoint("- item one\n") is True
    assert _at_safe_checkpoint("* item two\n") is True
    assert _at_safe_checkpoint("+ item three\n") is True
    assert _at_safe_checkpoint("1. ordered\n") is True
    assert _at_safe_checkpoint("  - nested item\n") is True


def test_blockquote_line_is_a_checkpoint() -> None:
    assert _at_safe_checkpoint("> a quote\n") is True


def test_horizontal_rule_is_a_checkpoint() -> None:
    assert _at_safe_checkpoint("---\n") is True
    assert _at_safe_checkpoint("***\n") is True
    assert _at_safe_checkpoint("___\n") is True
    assert _at_safe_checkpoint("--------\n") is True


def test_partial_bold_marker_is_NOT_a_checkpoint() -> None:
    """The headline test from the plan."""
    assert _at_safe_checkpoint("**Cla") is False


def test_mid_word_chunk_is_NOT_a_checkpoint() -> None:
    assert _at_safe_checkpoint("hello wor") is False


def test_arbitrary_paragraph_text_with_trailing_newline_is_NOT_a_checkpoint() -> None:
    """A sentence ending with one ``\\n`` doesn't count — only \\n\\n does."""
    assert _at_safe_checkpoint("just a normal paragraph line\n") is False


# ------------------------------------------------------------------
# _last_checkpoint_prefix — cache key generator
# ------------------------------------------------------------------


def test_no_checkpoint_yet_returns_empty_prefix() -> None:
    assert _last_checkpoint_prefix("partial token so far") == ""


def test_text_already_at_checkpoint_returns_itself() -> None:
    text = "first paragraph\n\n"
    assert _last_checkpoint_prefix(text) == text


def test_walks_back_to_last_paragraph_break() -> None:
    text = "paragraph one\n\nparagraph two so far"
    assert _last_checkpoint_prefix(text) == "paragraph one\n\n"


# ------------------------------------------------------------------
# AssistantTextMessage progressive render — App-level tests via Pilot
# ------------------------------------------------------------------


from textual.app import App, ComposeResult  # noqa: E402

from src.tui.widgets.messages.assistant_text import AssistantTextMessage  # noqa: E402


class _Harness(App):
    def compose(self) -> ComposeResult:
        self.row = AssistantTextMessage()
        yield self.row

    def append(self, chunk: str) -> None:
        self.row.append_chunk(chunk)

    def finalise(self, text: str) -> None:
        self.row.finalise(text)


def _body_renderable(row: AssistantTextMessage):
    """Read the last renderable handed to ``body.update(...)``.

    Uses the test-seam ``_last_body_renderable`` attribute rather than
    digging through Textual ``Static`` internals (which differ across
    Textual major versions).
    """

    return row._last_body_renderable


@pytest.mark.asyncio
async def test_plain_text_streams_without_markdown_render() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.append("hello ")
        app.append("world")
        await pilot.pause()
        rendered = _body_renderable(app.row)
        assert isinstance(rendered, Text)
        assert "hello world" in str(rendered)


@pytest.mark.asyncio
async def test_paragraph_checkpoint_triggers_markdown_render() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        # Stream until a paragraph break completes — first chunk lands plain
        # text, second chunk's trailing \n\n trips the checkpoint.
        app.append("**bold**\n")
        await pilot.pause()
        # No checkpoint yet; one trailing newline is not enough.
        assert isinstance(_body_renderable(app.row), Text)
        app.append("\n")
        await pilot.pause()
        rendered = _body_renderable(app.row)
        assert isinstance(rendered, Markdown)


@pytest.mark.asyncio
async def test_open_code_block_stays_plain_text() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.append("```python\n")
        app.append("x = 1")
        await pilot.pause()
        # Code block never closed → no checkpoint → still plain text.
        assert isinstance(_body_renderable(app.row), Text)


@pytest.mark.asyncio
async def test_closed_code_block_renders_markdown() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.append("```python\n")
        app.append("x = 1\n")
        app.append("```\n")
        await pilot.pause()
        rendered = _body_renderable(app.row)
        assert isinstance(rendered, Markdown)


@pytest.mark.asyncio
async def test_partial_tokens_dont_trigger_render() -> None:
    """Headline failure mode — chapter scenario.

    Streaming ``**Cla`` and then ``ude**`` must NOT mid-stream render the
    half-bold (Rich would emit literal ``**`` and produce visual artifacts).
    """

    app = _Harness()
    async with app.run_test() as pilot:
        app.append("**Cla")
        await pilot.pause()
        assert isinstance(_body_renderable(app.row), Text)
        app.append("ude**")
        await pilot.pause()
        # Still plain — no checkpoint completed yet.
        assert isinstance(_body_renderable(app.row), Text)
        app.append("\n\n")
        await pilot.pause()
        # Now paragraph break completes a checkpoint → render.
        rendered = _body_renderable(app.row)
        assert isinstance(rendered, Markdown)


@pytest.mark.asyncio
async def test_finalise_swaps_to_markdown_even_without_intermediate_checkpoint() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.append("# heading without trailing newline yet")
        await pilot.pause()
        # No checkpoint → still streaming plain text.
        assert isinstance(_body_renderable(app.row), Text)
        # finalise overrides — always renders Markdown for the final content.
        app.finalise("# heading without trailing newline yet")
        await pilot.pause()
        rendered = _body_renderable(app.row)
        assert isinstance(rendered, Markdown)


@pytest.mark.asyncio
async def test_post_finalise_chunks_are_discarded() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        # Use markdown-bearing content so the finalise path renders Markdown
        # (not a Text fast-path), which makes the type assertion meaningful.
        app.append("# heading paragraph\n\n")
        app.finalise("# heading paragraph\n\n")
        app.append("ignored chunk")
        await pilot.pause()
        rendered = _body_renderable(app.row)
        assert isinstance(rendered, Markdown)
        # The finalised content should be the only thing rendered; the
        # post-finalise chunk does not extend the body.
        assert app.row._final_text == "# heading paragraph\n\n"
        assert "ignored" not in app.row._final_text


# ------------------------------------------------------------------
# Cache-share assertion — chunks between checkpoints reuse cache entries
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunks_between_checkpoints_share_cache_entry() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        cache = get_markdown_cache()
        # First checkpoint: paragraph.
        app.append("intro paragraph\n\n")
        await pilot.pause()
        misses_after_first = cache.misses
        # Stream additional chunks past the checkpoint without crossing the
        # next one. Cache key uses the first paragraph's hash, so subsequent
        # renders should hit.
        app.append("starting next paragraph ")
        await pilot.pause()
        # No checkpoint reached yet on the trailing chunk → no extra render
        # call (refresh is plain text). Misses unchanged.
        assert cache.misses == misses_after_first


# ------------------------------------------------------------------
# Performance benchmarks (WI-1.3) — gated to CI to avoid dev-machine variance.
# ------------------------------------------------------------------


def _stream_into(cache: MarkdownCache, content: str, chunk_size: int = 16) -> None:
    """Simulate streaming by repeatedly hashing safe-checkpoint prefixes.

    Avoids Textual harness overhead for benchmarking the cache+checkpoint
    inner loop.
    """

    accumulated = ""
    for i in range(0, len(content), chunk_size):
        accumulated += content[i : i + chunk_size]
        if _at_safe_checkpoint(accumulated):
            prefix = _last_checkpoint_prefix(accumulated)
            from src.tui.markdown_cache import _hash as content_hash

            key = content_hash(prefix) if prefix else None
            cache.get_or_render(accumulated, cache_key=key)


@pytest.mark.skipif(
    not os.environ.get("CI"),
    reason="benchmarks are CI-gated to avoid dev-machine variance",
)
def test_typical_session_2k_chars_under_500ms() -> None:
    """A typical assistant response — markdown w/ paragraphs + code."""

    sample_para = (
        "This is a typical assistant paragraph with **bold** and `code` and "
        "[a link](https://example.com) plus some text to fill it out. "
    )
    content = (sample_para + "\n\n") * 14  # ~2K chars, 14 paragraphs.
    cache = MarkdownCache()
    start = time.perf_counter()
    _stream_into(cache, content)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"2K stream took {elapsed:.3f}s; expected < 0.5s"


@pytest.mark.skipif(
    not os.environ.get("CI"),
    reason="benchmarks are CI-gated to avoid dev-machine variance",
)
def test_worst_case_10k_chars_under_2s() -> None:
    """99th-percentile worst case."""

    sample_para = (
        "Worst-case paragraph with much more text, **bold**, `code`, and "
        "links to fill out a long assistant message that streams forever. "
    )
    content = (sample_para + "\n\n") * 70  # ~10K chars.
    cache = MarkdownCache()
    start = time.perf_counter()
    _stream_into(cache, content)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"10K stream took {elapsed:.3f}s; expected < 2.0s"
