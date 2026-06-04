"""Tests for ``src/tui/vim_find.py`` — ``f`` / ``F`` / ``t`` / ``T`` motions.

Phase 3 of the ch14 refactor. The string ``"hello world"`` has 'o' at
indices 4 and 7, 'l' at 2/3/9, ' ' at 5.
"""

from __future__ import annotations

from src.tui.vim_find import find_char


def test_f_forward_lands_on_char():
    """``f<o>`` from index 0 finds the first 'o' at index 4."""

    assert find_char("hello world", 0, "o", "f") == 4


def test_F_backward_lands_on_char():
    """``F<o>`` from index 9 finds the previous 'o' at index 7 (NOT 4)."""

    assert find_char("hello world", 9, "o", "F") == 7


def test_t_forward_stops_one_before():
    """``t<o>`` from index 0 lands at index 3 (one before 'o' at 4)."""

    assert find_char("hello world", 0, "o", "t") == 3


def test_T_backward_stops_one_after():
    """``T<o>`` from index 9 lands at index 8 (one after 'o' at 7)."""

    assert find_char("hello world", 9, "o", "T") == 8


def test_F_count_two_reaches_first_o():
    """``2F<o>`` from index 9 reaches the first 'o' at index 4."""

    assert find_char("hello world", 9, "o", "F", count=2) == 4


def test_T_count_two_reaches_one_after_first_o():
    """``2T<o>`` from index 9 reaches one after the first 'o' (index 5)."""

    assert find_char("hello world", 9, "o", "T", count=2) == 5


def test_count_repeats_forward():
    """``3f<a>`` in 'aaaa' from index 0 finds the third 'a' at index 3."""

    assert find_char("aaaa", 0, "a", "f", count=3) == 3


def test_not_found_returns_none():
    assert find_char("hello world", 0, "z", "f") is None
    assert find_char("hello world", 10, "z", "F") is None


def test_empty_line_returns_none():
    assert find_char("", 0, "a", "f") is None
    assert find_char("", 0, "a", "F") is None


def test_forward_no_room_returns_none():
    """``f<o>`` from the end of the line — no further 'o' forward."""

    assert find_char("hello world", 7, "o", "f") is None


def test_backward_from_zero_returns_none():
    """``F<o>`` from index 0 — nothing before the cursor to search."""

    assert find_char("hello world", 0, "o", "F") is None


def test_count_overshoots_returns_none():
    """``5f<a>`` in 'aaaa' from index 0 — only 3 'a's after cursor."""

    assert find_char("aaaa", 0, "a", "f", count=5) is None


def test_empty_char_returns_none():
    """Defensive: empty char string yields no match."""

    assert find_char("hello", 0, "", "f") is None
