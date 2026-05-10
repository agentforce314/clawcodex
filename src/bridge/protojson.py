"""Protojson-encoding helpers for CCR v2 wire-format quirks.

Per gap analysis §1 #21 universal rule: every CCR v2 endpoint that
returns an int64 may arrive as **string** OR **number** depending on the
encoder settings. ``protojson`` (Go) serializes int64 as string by
default to avoid JS precision loss; some endpoints serialize as number.

This helper normalizes both shapes into Python ``int``. Used by
``register_worker`` (WI-3.4) and ``fetch_remote_credentials`` (WI-3.3).

**Bounds:** matches TS ``Number.isSafeInteger`` (``±(2^53 - 1)``), NOT
the int64 range Python could otherwise represent. Wire-format symmetry
with the JS encoder is more important than Python's larger-int capacity:
the field is constrained at the JS-side encoder anyway, so accepting
larger values in Python would create a Python↔TS interop divergence the
server-side schema does not guarantee.
"""

from __future__ import annotations

# Per JS Number.isSafeInteger:
#   Number.MAX_SAFE_INTEGER = 2^53 - 1
#   Number.MIN_SAFE_INTEGER = -(2^53 - 1)
SAFE_INTEGER_MIN = -((2**53) - 1)
SAFE_INTEGER_MAX = (2**53) - 1

# Aliases kept for callers that prefer the int64 naming. Same values as
# the safe-integer pair so the wire-format symmetry holds. If a future
# server endpoint truly needs the full int64 range, add a separate
# ``coerce_full_int64`` helper rather than widening these.
INT64_MIN = SAFE_INTEGER_MIN
INT64_MAX = SAFE_INTEGER_MAX


def coerce_int64(value: object) -> int:
    """Return ``value`` as a Python ``int``, accepting string or int input.

    Raises ``ValueError`` if the value is neither, or if the parsed result
    overflows ``Number.isSafeInteger`` bounds (``±(2^53 - 1)``). Matches
    TS ``Number.isSafeInteger`` semantics for wire-format symmetry; see
    module docstring for the rationale.
    """
    if isinstance(value, bool):
        # bool is a subclass of int in Python; reject explicitly to avoid
        # accepting True/False as 1/0 from a wire payload.
        raise ValueError(f'coerce_int64: refusing bool: {value!r}')
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value, 10)
        except ValueError as exc:
            raise ValueError(f'coerce_int64: not a base-10 integer string: {value!r}') from exc
    else:
        raise ValueError(f'coerce_int64: unsupported type {type(value).__name__}: {value!r}')
    if not (SAFE_INTEGER_MIN <= parsed <= SAFE_INTEGER_MAX):
        raise ValueError(
            f'coerce_int64: out of safe-integer range (TS Number.isSafeInteger): {parsed}'
        )
    return parsed


__all__ = ['INT64_MAX', 'INT64_MIN', 'SAFE_INTEGER_MAX', 'SAFE_INTEGER_MIN', 'coerce_int64']
