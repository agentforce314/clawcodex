"""In-memory stash for the keychain prefetch result.

Chapter 2 round-2 PR-1 (G1): the keychain prefetch fires at module
import (`src/cli.py:22-31`) but the result was never consumed. This
module gives `init()` somewhere to park the value so the auth/OAuth
layer can pick it up lazily without re-shelling-out to ``security``.

The stash is module-private state; first non-None set wins (idempotent
under the memoized ``init()``). On platforms where the prefetch yields
``None`` (non-macOS, ``security`` unavailable), readers see ``None`` and
fall back to interactive credential resolution — same posture as TS.

See:
  - ``claude-code-from-source/book/ch02-bootstrap.md`` §"Phase 1"
  - ``my-docs/ch02-bootstrap-gap-analysis.md`` §G1
  - ``my-docs/ch02-bootstrap-refactoring-plan.md`` W1.2
"""

from __future__ import annotations

import os

__all__ = [
    "stash_keychain_credentials",
    "read_stashed_keychain",
    "reset_stashed_keychain_for_test_only",
]


_KEYCHAIN_VALUE: str | None = None


def stash_keychain_credentials(value: str | None) -> None:
    """Park a keychain prefetch result for later consumers.

    First non-``None`` value wins. Subsequent calls are silent no-ops
    so that the memoized ``init()`` can call this from multiple entry
    points without clobbering an earlier successful read.
    """
    global _KEYCHAIN_VALUE
    if _KEYCHAIN_VALUE is None and value is not None:
        _KEYCHAIN_VALUE = value


def read_stashed_keychain() -> str | None:
    """Return the stashed keychain value, or ``None`` if unset."""
    return _KEYCHAIN_VALUE


def reset_stashed_keychain_for_test_only() -> None:
    """Reset the stash. Test-only — gated by ``PYTEST_CURRENT_TEST``."""
    if os.environ.get("PYTEST_CURRENT_TEST") is None:
        raise RuntimeError(
            "reset_stashed_keychain_for_test_only can only be called in tests"
        )
    global _KEYCHAIN_VALUE
    _KEYCHAIN_VALUE = None
