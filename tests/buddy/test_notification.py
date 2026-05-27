"""Date-gates + buddy-trigger regex."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from src.buddy.notification import (
    find_buddy_trigger_positions,
    is_buddy_live,
    is_buddy_teaser_window,
)


def _freeze(year: int, month: int, day: int) -> object:
    """Helper to patch datetime.now() inside notification module."""
    fake = datetime(year, month, day, 12, 0, 0)

    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None) -> 'datetime':
            return fake

    return patch('src.buddy.notification.datetime', _FakeDateTime)


def test_teaser_window_april_3_2026_true() -> None:
    with _freeze(2026, 4, 3):
        assert is_buddy_teaser_window() is True


def test_teaser_window_april_8_2026_false() -> None:
    with _freeze(2026, 4, 8):
        assert is_buddy_teaser_window() is False


def test_teaser_window_march_31_2026_false() -> None:
    with _freeze(2026, 3, 31):
        assert is_buddy_teaser_window() is False


def test_teaser_window_april_2027_false() -> None:
    """Only April 1-7 of 2026 — not later years."""
    with _freeze(2027, 4, 3):
        assert is_buddy_teaser_window() is False


def test_buddy_live_april_2026_true() -> None:
    with _freeze(2026, 4, 15):
        assert is_buddy_live() is True


def test_buddy_live_may_2026_true() -> None:
    with _freeze(2026, 5, 27):
        assert is_buddy_live() is True


def test_buddy_live_march_2026_false() -> None:
    with _freeze(2026, 3, 15):
        assert is_buddy_live() is False


def test_buddy_live_2027_true() -> None:
    with _freeze(2027, 1, 1):
        assert is_buddy_live() is True


def test_find_triggers_basic() -> None:
    result = find_buddy_trigger_positions('hi /buddy there')
    assert result == [{'start': 3, 'end': 9}]


def test_find_triggers_word_boundary() -> None:
    """``/buddyfoo`` should not match — word boundary required."""
    assert find_buddy_trigger_positions('hi /buddyfoo') == []


def test_find_triggers_multiple() -> None:
    result = find_buddy_trigger_positions('/buddy and /buddy')
    assert len(result) == 2
    assert result[0]['start'] == 0
    assert result[1]['start'] == 11


def test_find_triggers_empty_text() -> None:
    assert find_buddy_trigger_positions('') == []


def test_find_triggers_no_match() -> None:
    assert find_buddy_trigger_positions('nothing special') == []
