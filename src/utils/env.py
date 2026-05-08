"""Environment-variable helpers — Chunk G / WI-8.1 prerequisite.

Mirrors ``typescript/src/utils/envUtils.ts``. ``is_env_truthy`` was
duplicated in ``src/agent/fork_subagent.py:_is_env_truthy``; per the
critic's Chunk-F note we hoist it here as the canonical location and
re-export from fork_subagent for back-compat.

The canonical truthy set matches the TS spirit (``"1"``, ``"true"``,
``"yes"``, ``"on"``, case-insensitive after stripping). Using a
constant frozenset rather than membership tests against a tuple gives
O(1) lookup and pins the set to "exactly these values."
"""
from __future__ import annotations

import os
from typing import Final

_TRUTHY_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def is_env_truthy(name: str) -> bool:
    """Return True iff ``os.environ[name]`` is set to a truthy value.

    Mirrors ``isEnvTruthy`` in ``typescript/src/utils/envUtils.ts``.
    Unset / empty / whitespace-only values are False; the canonical
    accepted truthy strings are ``"1"``, ``"true"``, ``"yes"``, ``"on"``
    after lowercasing and stripping.
    """
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY_VALUES


__all__ = [
    "is_env_truthy",
]
