"""Tests for Phase-4 wave-1 multi-line vim modules.

Covers:
- :class:`VimBuffer` cursor + replace/delete/insert
- :func:`find_text_object` for words / paragraphs / quotes / braces
- :func:`parse_operator_motion`
- :func:`apply_operator` for d / c / y composed with motions and text objects
- count prefixes (``d2w``)
"""

from __future__ import annotations

import pytest

from src.tui.vim_buffer import Cursor, Range, VimBuffer
from src.tui.vim_operators import (
    ParsedOperator,
    apply_operator,
    parse_operator_motion,
    resolve_target_range,
)
from src.tui.vim_text_objects import find_text_object


# ------------------------------------------------------------------
# VimBuffer
# ------------------------------------------------------------------


def test_buffer_empty_is_one_blank_line() -> None:
    buf = VimBuffer()
    assert buf.line_count == 1
    assert buf.lines == [""]
    assert buf.is_empty()


def test_buffer_splits_on_newlines() -> None:
    buf = VimBuffer("a\nb\nc")
    assert buf.lines == ["a", "b", "c"]


def test_set_cursor_clamps() -> None:
    buf = VimBuffer("hello")
    buf.set_cursor(99, 99)
    assert buf.cursor == Cursor(0, 5)
    buf.set_cursor(-1, -1)
    assert buf.cursor == Cursor(0, 0)


def test_text_in_single_line() -> None:
    buf = VimBuffer("hello world")
    rng = Range(Cursor(0, 0), Cursor(0, 5))
    assert buf.text_in(rng) == "hello"


def test_text_in_multi_line() -> None:
    buf = VimBuffer("line one\nline two\nline three")
    rng = Range(Cursor(0, 5), Cursor(2, 4))
    assert buf.text_in(rng) == "one\nline two\nline"


def test_replace_single_line() -> None:
    buf = VimBuffer("hello world")
    rng = Range(Cursor(0, 0), Cursor(0, 5))
    removed = buf.replace(rng, "yo")
    assert removed == "hello"
    assert buf.text == "yo world"


def test_replace_multi_line_collapses() -> None:
    buf = VimBuffer("a\nb\nc")
    rng = Range(Cursor(0, 0), Cursor(2, 1))
    buf.replace(rng, "X")
    assert buf.text == "X"


def test_insert_advances_cursor() -> None:
    buf = VimBuffer("hello")
    buf.set_cursor(0, 5)
    buf.insert(" world")
    assert buf.text == "hello world"
    assert buf.cursor == Cursor(0, 11)


def test_insert_with_newline_advances_to_new_line() -> None:
    buf = VimBuffer("hi")
    buf.set_cursor(0, 2)
    buf.insert("\nbye")
    assert buf.text == "hi\nbye"
    assert buf.cursor == Cursor(1, 3)


# ------------------------------------------------------------------
# find_text_object — word
# ------------------------------------------------------------------


def test_iw_inner_word_returns_word_only() -> None:
    buf = VimBuffer("hello world")
    rng = find_text_object(buf, Cursor(0, 7), "iw")
    assert rng is not None
    assert buf.text_in(rng) == "world"


def test_aw_around_word_includes_trailing_space() -> None:
    buf = VimBuffer("hello world")
    rng = find_text_object(buf, Cursor(0, 0), "aw")
    assert rng is not None
    assert buf.text_in(rng) == "hello "


def test_iw_on_separator_returns_none() -> None:
    """Cursor on a separator (space) — wave-1 returns None."""

    buf = VimBuffer("a b")
    assert find_text_object(buf, Cursor(0, 1), "iw") is None


# ------------------------------------------------------------------
# find_text_object — quoted
# ------------------------------------------------------------------


def test_inner_double_quoted() -> None:
    buf = VimBuffer('say "hello world" please')
    rng = find_text_object(buf, Cursor(0, 7), 'i"')
    assert rng is not None
    assert buf.text_in(rng) == "hello world"


def test_around_double_quoted_includes_quotes() -> None:
    buf = VimBuffer('say "hello" please')
    rng = find_text_object(buf, Cursor(0, 7), 'a"')
    assert rng is not None
    assert buf.text_in(rng) == '"hello"'


def test_quotes_with_no_pair_returns_none() -> None:
    buf = VimBuffer('only one " here')
    assert find_text_object(buf, Cursor(0, 5), 'i"') is None


# ------------------------------------------------------------------
# find_text_object — brace
# ------------------------------------------------------------------


def test_inner_brace_block() -> None:
    buf = VimBuffer("foo(bar baz) qux")
    rng = find_text_object(buf, Cursor(0, 6), "i(")
    assert rng is not None
    assert buf.text_in(rng) == "bar baz"


def test_around_brace_block() -> None:
    buf = VimBuffer("foo(bar) qux")
    rng = find_text_object(buf, Cursor(0, 5), "a(")
    assert rng is not None
    assert buf.text_in(rng) == "(bar)"


def test_nested_brace_picks_inner() -> None:
    buf = VimBuffer("a (b (c) d)")
    # Cursor inside inner (c) — should pick the inner pair.
    rng = find_text_object(buf, Cursor(0, 6), "i(")
    assert rng is not None
    assert buf.text_in(rng) == "c"


def test_brace_alias_close_bracket_works() -> None:
    """``i]`` should produce the same result as ``i[``."""

    buf = VimBuffer("[abc]")
    rng_open = find_text_object(buf, Cursor(0, 2), "i[")
    rng_close = find_text_object(buf, Cursor(0, 2), "i]")
    assert rng_open == rng_close


# ------------------------------------------------------------------
# find_text_object — paragraph
# ------------------------------------------------------------------


def test_inner_paragraph() -> None:
    buf = VimBuffer("first para\ncontinued\n\nnext para\n\nthird")
    rng = find_text_object(buf, Cursor(0, 0), "ip")
    assert rng is not None
    text = buf.text_in(rng)
    # Paragraph is rows 0-1 (non-blank consecutive lines).
    assert "first para" in text
    assert "continued" in text
    assert "next para" not in text


# ------------------------------------------------------------------
# parse_operator_motion
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "input,expected",
    [
        ("dw", ParsedOperator("d", "w", 1)),
        ("d2w", ParsedOperator("d", "w", 2)),
        ("ciw", ParsedOperator("c", "iw", 1)),
        ("y$", ParsedOperator("y", "$", 1)),
        ('da"', ParsedOperator("d", 'a"', 1)),
        ("c5w", ParsedOperator("c", "w", 5)),
    ],
)
def test_parse_operator_motion_valid(input: str, expected: ParsedOperator) -> None:
    assert parse_operator_motion(input) == expected


@pytest.mark.parametrize(
    "input",
    [
        "",
        "z",  # bad operator
        "dz",  # bad motion
        "d",  # missing target
        "dgg",  # 2-char target that isn't a text object
        # Critic-flagged: tighten the text-object char class so non-object
        # 2-char targets are rejected.
        "dia",
        "dii",
        "caa",
        "cai",
        "dao",  # 'o' is not a text-object kind
    ],
)
def test_parse_operator_motion_invalid(input: str) -> None:
    assert parse_operator_motion(input) is None


# ------------------------------------------------------------------
# apply_operator
# ------------------------------------------------------------------


def test_dw_deletes_one_word() -> None:
    buf = VimBuffer("hello world")
    rng = resolve_target_range(buf, Cursor(0, 0), "w", count=1)
    assert rng is not None
    apply_operator(buf, rng, "d")
    assert buf.text == "world"


def test_d2w_deletes_two_words() -> None:
    buf = VimBuffer("alpha beta gamma")
    rng = resolve_target_range(buf, Cursor(0, 0), "w", count=2)
    assert rng is not None
    apply_operator(buf, rng, "d")
    assert buf.text == "gamma"


def test_dollar_deletes_to_end_of_line() -> None:
    buf = VimBuffer("abc def ghi")
    buf.set_cursor(0, 4)
    rng = resolve_target_range(buf, buf.cursor, "$", count=1)
    assert rng is not None
    apply_operator(buf, rng, "d")
    assert buf.text == "abc "


def test_yank_does_not_modify_buffer() -> None:
    buf = VimBuffer("hello world")
    rng = resolve_target_range(buf, Cursor(0, 0), "w", count=1)
    yank: list[str] = []
    apply_operator(buf, rng, "y", yank_buffer=yank)
    assert buf.text == "hello world"
    assert yank == ["hello "]


def test_change_clears_range() -> None:
    buf = VimBuffer("hello world")
    rng = find_text_object(buf, Cursor(0, 0), "iw")
    assert rng is not None
    apply_operator(buf, rng, "c")
    assert buf.text == " world"


def test_delete_with_text_object_iw() -> None:
    buf = VimBuffer("alpha bravo charlie")
    rng = find_text_object(buf, Cursor(0, 6), "iw")
    assert rng is not None
    apply_operator(buf, rng, "d")
    assert buf.text == "alpha  charlie"


def test_indent_operator_adds_two_spaces() -> None:
    buf = VimBuffer("first\nsecond")
    rng = Range(Cursor(0, 0), Cursor(1, 6))
    apply_operator(buf, rng, ">")
    assert buf.text == "  first\n  second"


def test_dedent_operator_removes_leading_spaces() -> None:
    buf = VimBuffer("    first\n    second")
    rng = Range(Cursor(0, 0), Cursor(1, 10))
    apply_operator(buf, rng, "<")
    assert buf.text == "  first\n  second"


# ------------------------------------------------------------------
# Composition smoke tests: parse + resolve + apply
# ------------------------------------------------------------------


def test_full_pipeline_d_iw() -> None:
    buf = VimBuffer("alpha bravo charlie")
    parsed = parse_operator_motion("diw")
    assert parsed is not None
    rng = resolve_target_range(buf, Cursor(0, 6), parsed.target, count=parsed.count)
    assert rng is not None
    apply_operator(buf, rng, parsed.operator)
    assert buf.text == "alpha  charlie"


def test_full_pipeline_c_a_quote() -> None:
    buf = VimBuffer('say "the words" please')
    parsed = parse_operator_motion('da"')
    assert parsed is not None
    rng = resolve_target_range(buf, Cursor(0, 7), parsed.target)
    assert rng is not None
    apply_operator(buf, rng, parsed.operator)
    assert buf.text == "say  please"
