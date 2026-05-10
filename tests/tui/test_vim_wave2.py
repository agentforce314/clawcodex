"""Tests for Phase-4 wave-2: Visual mode + regex search."""

from __future__ import annotations

import pytest

from src.tui.vim_buffer import Cursor, VimBuffer
from src.tui.vim_operators import apply_operator
from src.tui.vim_search import (
    InvalidPattern,
    SearchDirection,
    VimSearchState,
    find_next,
)
from src.tui.vim_visual import VisualMode, VisualSelection, VisualState


# ------------------------------------------------------------------
# VisualSelection — character mode
# ------------------------------------------------------------------


def test_character_visual_normalises_anchor_after_cursor() -> None:
    """Vim Visual is character-INCLUSIVE — anchor at col 6, cursor at col 0
    means cols 0..6 are selected (7 chars)."""

    buf = VimBuffer("hello world")
    sel = VisualSelection(
        mode=VisualMode.CHARACTER,
        anchor=Cursor(0, 6),
        cursor=Cursor(0, 0),
    )
    rng = sel.as_range(buf)
    # 7 chars starting at col 0: "hello w".
    assert buf.text_in(rng) == "hello w"


def test_character_visual_inclusive_end_one_char() -> None:
    """Vim Visual is character-inclusive — a single-char selection covers
    one character."""

    buf = VimBuffer("abcdef")
    sel = VisualSelection(
        mode=VisualMode.CHARACTER,
        anchor=Cursor(0, 2),
        cursor=Cursor(0, 2),
    )
    rng = sel.as_range(buf)
    assert buf.text_in(rng) == "c"


def test_character_visual_across_lines() -> None:
    buf = VimBuffer("alpha\nbeta\ngamma")
    sel = VisualSelection(
        mode=VisualMode.CHARACTER,
        anchor=Cursor(0, 2),
        cursor=Cursor(2, 1),
    )
    rng = sel.as_range(buf)
    assert buf.text_in(rng) == "pha\nbeta\nga"


# ------------------------------------------------------------------
# VisualSelection — line mode
# ------------------------------------------------------------------


def test_line_visual_covers_whole_lines() -> None:
    buf = VimBuffer("first\nsecond\nthird")
    sel = VisualSelection(
        mode=VisualMode.LINE,
        anchor=Cursor(0, 3),
        cursor=Cursor(1, 1),
    )
    rng = sel.as_range(buf)
    assert buf.text_in(rng) == "first\nsecond"


def test_line_visual_normalises_when_cursor_above_anchor() -> None:
    buf = VimBuffer("a\nb\nc")
    sel = VisualSelection(
        mode=VisualMode.LINE,
        anchor=Cursor(2, 0),
        cursor=Cursor(0, 0),
    )
    rng = sel.as_range(buf)
    assert buf.text_in(rng) == "a\nb\nc"


# ------------------------------------------------------------------
# VisualSelection — block mode
# ------------------------------------------------------------------


def test_block_visual_per_line_ranges_for_rectangle() -> None:
    buf = VimBuffer("aaaaa\nbbbbb\nccccc")
    sel = VisualSelection(
        mode=VisualMode.BLOCK,
        anchor=Cursor(0, 1),
        cursor=Cursor(2, 3),
    )
    ranges = sel.block_ranges(buf)
    assert len(ranges) == 3
    assert buf.text_in(ranges[0]) == "aaa"
    assert buf.text_in(ranges[1]) == "bbb"
    assert buf.text_in(ranges[2]) == "ccc"


def test_block_visual_handles_short_lines() -> None:
    """Visual-Block over rows of varying length — short lines yield empty."""

    buf = VimBuffer("aaa\nb\nccc")
    sel = VisualSelection(
        mode=VisualMode.BLOCK,
        anchor=Cursor(0, 0),
        cursor=Cursor(2, 2),
    )
    ranges = sel.block_ranges(buf)
    assert buf.text_in(ranges[0]) == "aaa"
    # Short line `b` only has one char; block clamps to len(line).
    assert buf.text_in(ranges[1]) == "b"
    assert buf.text_in(ranges[2]) == "ccc"


# ------------------------------------------------------------------
# VisualState — toggle lifecycle
# ------------------------------------------------------------------


def test_visual_state_lifecycle() -> None:
    state = VisualState()
    assert state.is_active() is False
    state.start(VisualMode.CHARACTER, anchor=Cursor(0, 0))
    assert state.is_active()
    state.update_cursor(Cursor(0, 5))
    assert state.selection is not None
    assert state.selection.cursor == Cursor(0, 5)
    state.exit()
    assert state.is_active() is False


def test_visual_state_with_operator_applies_to_buffer() -> None:
    """End-to-end: select chars, apply ``d`` operator, verify deletion."""

    buf = VimBuffer("hello world")
    state = VisualState()
    state.start(VisualMode.CHARACTER, anchor=Cursor(0, 0))
    state.update_cursor(Cursor(0, 4))
    rng = state.selection.as_range(buf)  # type: ignore[union-attr]
    apply_operator(buf, rng, "d")
    assert buf.text == " world"


# ------------------------------------------------------------------
# find_next — forward
# ------------------------------------------------------------------


def test_find_next_forward_simple() -> None:
    buf = VimBuffer("alpha bravo alpha charlie")
    hit = find_next(buf, pattern="alpha", after=Cursor(0, 0))
    assert hit is not None
    assert hit.at == Cursor(0, 12)  # second 'alpha'
    assert hit.text == "alpha"


def test_find_next_forward_no_match() -> None:
    buf = VimBuffer("abc")
    assert find_next(buf, pattern="xyz", after=Cursor(0, 0)) is None


def test_find_next_forward_wrap() -> None:
    """Past the last match → wrap to beginning."""

    buf = VimBuffer("foo bar foo")
    # Cursor sits past the last 'foo' (col 8 = 'foo' start; cursor 10
    # is past it). Wrap should find the first 'foo' at col 0.
    hit = find_next(buf, pattern="foo", after=Cursor(0, 10))
    assert hit is not None
    assert hit.at == Cursor(0, 0)


def test_find_next_forward_no_wrap_returns_none() -> None:
    buf = VimBuffer("foo bar foo")
    hit = find_next(
        buf, pattern="foo", after=Cursor(0, 10), wrap=False
    )
    assert hit is None


def test_find_next_forward_multiline() -> None:
    buf = VimBuffer("line one\nline two foo\nline three")
    hit = find_next(buf, pattern="foo", after=Cursor(0, 0))
    assert hit is not None
    assert hit.at == Cursor(1, 9)


# ------------------------------------------------------------------
# find_next — backward
# ------------------------------------------------------------------


def test_find_next_backward_simple() -> None:
    buf = VimBuffer("alpha bravo alpha charlie")
    # From end of buffer backward — first alpha hit is the second 'alpha'.
    hit = find_next(
        buf,
        pattern="alpha",
        after=Cursor(0, 18),
        direction=SearchDirection.BACKWARD,
    )
    assert hit is not None
    assert hit.at == Cursor(0, 12)


def test_find_next_backward_picks_previous_on_same_line() -> None:
    buf = VimBuffer("foo bar foo")
    hit = find_next(
        buf,
        pattern="foo",
        after=Cursor(0, 8),  # right at the second 'foo' start
        direction=SearchDirection.BACKWARD,
    )
    assert hit is not None
    # Backward from col 8 — the first 'foo' at col 0 is the previous match.
    assert hit.at == Cursor(0, 0)


def test_find_next_backward_wrap() -> None:
    buf = VimBuffer("foo bar")
    hit = find_next(
        buf,
        pattern="foo",
        after=Cursor(0, 0),
        direction=SearchDirection.BACKWARD,
    )
    assert hit is not None
    assert hit.at == Cursor(0, 0)


# ------------------------------------------------------------------
# Pattern errors
# ------------------------------------------------------------------


def test_invalid_pattern_raises() -> None:
    buf = VimBuffer("abc")
    with pytest.raises(InvalidPattern):
        find_next(buf, pattern="(unbalanced", after=Cursor(0, 0))


def test_empty_pattern_returns_none() -> None:
    buf = VimBuffer("abc")
    assert find_next(buf, pattern="", after=Cursor(0, 0)) is None


# ------------------------------------------------------------------
# VimSearchState — n / N repeat
# ------------------------------------------------------------------


def test_search_state_repeat_forward() -> None:
    buf = VimBuffer("foo bar foo baz foo")
    state = VimSearchState()
    hit1 = state.search(buf, pattern="foo", at=Cursor(0, 0))
    assert hit1 is not None
    assert hit1.at == Cursor(0, 8)
    # n: next forward
    hit2 = state.repeat(buf, at=hit1.at)
    assert hit2 is not None
    assert hit2.at == Cursor(0, 16)


def test_search_state_repeat_reverse() -> None:
    """``N`` reverses the original direction."""

    buf = VimBuffer("foo bar foo baz foo")
    state = VimSearchState()
    state.search(buf, pattern="foo", at=Cursor(0, 0))
    # Step the cursor to past the third foo. ``N`` should walk backward.
    hit = state.repeat(buf, at=Cursor(0, 18), reverse=True)
    assert hit is not None
    assert hit.at == Cursor(0, 16)


def test_search_state_repeat_with_no_prior_search_returns_none() -> None:
    buf = VimBuffer("foo")
    state = VimSearchState()
    assert state.repeat(buf, at=Cursor(0, 0)) is None
    assert state.is_armed() is False


def test_backward_then_n_continues_backward() -> None:
    buf = VimBuffer("foo bar foo baz foo")
    state = VimSearchState()
    state.search(
        buf,
        pattern="foo",
        at=Cursor(0, 18),
        direction=SearchDirection.BACKWARD,
    )
    # n in backward mode = continue backward
    hit = state.repeat(buf, at=Cursor(0, 12))
    assert hit is not None
    assert hit.at == Cursor(0, 8)


def test_search_with_regex_special_chars() -> None:
    buf = VimBuffer("class Foo:\n  def bar():\n    pass")
    hit = find_next(
        buf, pattern=r"^\s+def\s+\w+", after=Cursor(0, 0)
    )
    # Search across newlines — multi-line pattern. Python's re by
    # default doesn't span newlines without DOTALL or MULTILINE; our
    # impl walks line-by-line so ``^`` matches at line start.
    assert hit is not None
    assert hit.at.row == 1
