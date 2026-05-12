"""Helpers for ``--bare`` (CLAUDE_CODE_SIMPLE) mode.

Mirrors TS ``isBareMode()`` / ``CLAUDE_CODE_SIMPLE`` env-var gating
patterns from ``typescript/src/utils/envUtils.ts:isBareMode``. The
chapter §"Phase 0: Fast-Path Dispatch" mentions ``--bare`` as a major
performance lever; the TS reference uses it to skip hooks, plugin sync,
attribution, auto-memory, background prefetches, and keychain reads.

Plan reference: ``my-docs/ch02-bootstrap-refactoring-plan.md`` Phase 4.

Bare mode is a coarse "minimal" toggle the user opts into for
scripted / CI / pipe-only invocations where the heavy TUI / plugin /
auto-memory machinery is wasted overhead. The flag is read both via
argparse (``args.bare``) and via the ``CLAUDE_CODE_SIMPLE`` env var —
the env-var form is what subsystems gate on, so callers can also force
bare mode by setting the env before invoking clawcodex.
"""

from __future__ import annotations

import os

__all__ = [
    "BARE_MODE_ENV_VAR",
    "is_bare_mode",
    "set_bare_mode_env",
]


BARE_MODE_ENV_VAR = "CLAUDE_CODE_SIMPLE"


def is_bare_mode() -> bool:
    """True iff the current process is running in bare mode.

    Reads ``CLAUDE_CODE_SIMPLE`` from the environment. The truthy
    values match ``isEnvTruthy`` from the TS reference:
    ``1`` / ``true`` / ``yes`` (case-insensitive).
    """
    raw = os.environ.get(BARE_MODE_ENV_VAR, "")
    return raw.strip().lower() in {"1", "true", "yes"}


def set_bare_mode_env() -> None:
    """Set ``CLAUDE_CODE_SIMPLE=1`` in the environment.

    Called from ``cli.main()`` early (before ``init()``) when the
    ``--bare`` flag is detected. Subsystems that gate on
    ``is_bare_mode()`` then see the bit and skip their work.

    Idempotent.
    """
    os.environ[BARE_MODE_ENV_VAR] = "1"
