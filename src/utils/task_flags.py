"""Helpers that port the TaskV2 / TodoWrite gating logic from TypeScript.

This mirrors a small slice of ``typescript/src/utils/tasks.ts`` — specifically
``isTodoV2Enabled()`` — so that we expose the same tool set to the model:

* Interactive sessions (REPL / TUI) expose ``TaskCreate``, ``TaskGet``,
  ``TaskUpdate`` and ``TaskList`` and hide ``TodoWrite``.
* Non-interactive headless / SDK sessions expose ``TodoWrite`` and hide the
  TaskV2 tools (unless ``CLAUDE_CODE_ENABLE_TASKS`` is set, mirroring the
  env-based opt-in in the TypeScript reference).
"""

from __future__ import annotations

import os

from src.bootstrap.state import get_is_non_interactive_session

_TRUTHY = {"1", "true", "yes", "on", "y", "t"}
_FALSY = {"0", "false", "no", "off", "n", "f", ""}


def _env_truthy(name: str) -> bool:
    """Matches ``isEnvTruthy`` in ``typescript/src/utils/envUtils.ts``.

    Returns ``True`` only when the environment variable is set to a recognised
    truthy value. Unset variables return ``False``. The test is
    case-insensitive.
    """
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


def is_todo_v2_enabled() -> bool:
    """Port of ``isTodoV2Enabled`` from the TypeScript implementation.

    * Force-enabled when ``CLAUDE_CODE_ENABLE_TASKS`` is truthy (e.g. SDK users
      who prefer the TaskV2 tools).
    * Otherwise enabled only for interactive sessions (the default for
      ``clawcodex`` REPL and TUI).
    """
    if _env_truthy("CLAUDE_CODE_ENABLE_TASKS"):
        return True
    return not get_is_non_interactive_session()


__all__ = ["is_todo_v2_enabled"]
