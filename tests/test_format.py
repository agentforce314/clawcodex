"""Parity tests for ``src.utils.format``.

These mirror the behaviour of ``typescript/src/utils/format.ts`` so the
REPL spinner row produces byte-identical output across implementations.
"""

from __future__ import annotations

import pytest

from src.utils.format import format_duration, format_number, format_tokens


@pytest.mark.parametrize(
    "ms,expected",
    [
        (0, "0s"),
        (500, "0s"),
        (999, "0s"),
        (1_000, "1s"),
        (12_345, "12s"),
        (59_999, "59s"),
        (60_000, "1m 0s"),
        (90_000, "1m 30s"),
        (3_599_000, "59m 59s"),
        (3_600_000, "1h 0m 0s"),
        (3_660_500, "1h 1m 1s"),
        (86_400_000, "1d 0h 0m"),
        (90_061_000, "1d 1h 1m"),
    ],
)
def test_format_duration(ms: int, expected: str) -> None:
    assert format_duration(ms) == expected


@pytest.mark.parametrize(
    "ms,expected",
    [
        (90_000, "1m"),
        (3_600_000, "1h"),
        (86_400_000, "1d"),
        (45_000, "45s"),
    ],
)
def test_format_duration_most_significant_only(ms: int, expected: str) -> None:
    assert format_duration(ms, most_significant_only=True) == expected


def test_format_duration_hide_trailing_zeros() -> None:
    assert format_duration(3_600_000, hide_trailing_zeros=True) == "1h"
    assert format_duration(3_660_000, hide_trailing_zeros=True) == "1h 1m"
    assert format_duration(86_400_000, hide_trailing_zeros=True) == "1d"
    assert format_duration(90_000_000, hide_trailing_zeros=True) == "1d 1h"


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "0"),
        (1, "1"),
        (900, "900"),
        (999, "999"),
        (1_000, "1.0k"),
        (1_321, "1.3k"),
        (12_500, "12.5k"),
        (999_999, "1000.0k"),
        (1_000_000, "1.0m"),
        (1_500_000, "1.5m"),
        (2_000_000_000, "2.0b"),
    ],
)
def test_format_number(n: int, expected: str) -> None:
    assert format_number(n) == expected


def test_format_tokens_drops_trailing_zero() -> None:
    assert format_tokens(1_000) == "1k"
    assert format_tokens(1_321) == "1.3k"
    assert format_tokens(900) == "900"
