"""Tests for ``src.bridge.protojson.coerce_int64``.

Bounds match TS ``Number.isSafeInteger`` (``±(2^53 - 1)``); see module
docstring. ``INT64_*`` aliases point at the same values for wire-format
symmetry.
"""

from __future__ import annotations

import pytest

from src.bridge.protojson import (
    INT64_MAX,
    INT64_MIN,
    SAFE_INTEGER_MAX,
    SAFE_INTEGER_MIN,
    coerce_int64,
)


def test_int_passthrough() -> None:
    assert coerce_int64(0) == 0
    assert coerce_int64(42) == 42
    assert coerce_int64(-1) == -1


def test_string_parsed_as_base10() -> None:
    assert coerce_int64('0') == 0
    assert coerce_int64('42') == 42
    assert coerce_int64('-1') == -1
    # Whitespace is permitted by Python int(); explicitly test.
    assert coerce_int64(' 7 ') == 7


def test_int64_aliases_match_safe_integer() -> None:
    """INT64_MIN/MAX alias the safe-integer pair (Number.isSafeInteger)."""
    assert INT64_MAX == SAFE_INTEGER_MAX == (2**53) - 1
    assert INT64_MIN == SAFE_INTEGER_MIN == -((2**53) - 1)


def test_safe_integer_bounds_accepted() -> None:
    assert coerce_int64(SAFE_INTEGER_MAX) == SAFE_INTEGER_MAX
    assert coerce_int64(SAFE_INTEGER_MIN) == SAFE_INTEGER_MIN
    assert coerce_int64(str(SAFE_INTEGER_MAX)) == SAFE_INTEGER_MAX
    assert coerce_int64(str(SAFE_INTEGER_MIN)) == SAFE_INTEGER_MIN


def test_safe_integer_overflow_rejected() -> None:
    with pytest.raises(ValueError, match='out of safe-integer range'):
        coerce_int64(SAFE_INTEGER_MAX + 1)
    with pytest.raises(ValueError, match='out of safe-integer range'):
        coerce_int64(SAFE_INTEGER_MIN - 1)
    with pytest.raises(ValueError, match='out of safe-integer range'):
        coerce_int64(str(SAFE_INTEGER_MAX + 1))


def test_2_pow_53_to_2_pow_63_now_rejected() -> None:
    """Values in (2^53-1, 2^63) used to be accepted under int64 bounds.

    Per critic #4 + the round-4 plan revision: matching TS
    ``Number.isSafeInteger`` for wire-format symmetry. Any port-author
    expecting the old ±2^63 envelope must now coerce explicitly.
    """
    with pytest.raises(ValueError, match='out of safe-integer range'):
        coerce_int64(2**60)
    with pytest.raises(ValueError, match='out of safe-integer range'):
        coerce_int64(str(2**60))


def test_invalid_string_rejected() -> None:
    with pytest.raises(ValueError, match='not a base-10 integer string'):
        coerce_int64('hello')
    with pytest.raises(ValueError, match='not a base-10 integer string'):
        coerce_int64('1.5')
    with pytest.raises(ValueError, match='not a base-10 integer string'):
        coerce_int64('0x10')


def test_unsupported_type_rejected() -> None:
    with pytest.raises(ValueError, match='unsupported type'):
        coerce_int64(1.5)
    with pytest.raises(ValueError, match='unsupported type'):
        coerce_int64(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='unsupported type'):
        coerce_int64([1])  # type: ignore[arg-type]


def test_bool_explicitly_rejected() -> None:
    """``bool`` is a subclass of int in Python; must be rejected explicitly."""
    with pytest.raises(ValueError, match='refusing bool'):
        coerce_int64(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match='refusing bool'):
        coerce_int64(False)  # type: ignore[arg-type]
