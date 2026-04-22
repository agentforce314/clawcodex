"""Safety gate for ``--dangerously-skip-permissions``.

Mirrors ``typescript/src/setup.ts:382-401``. The bypass flag must not be
silently honored when the process is running with elevated privileges
(root/sudo) outside a sandboxed environment — that combination would let
the agent perform arbitrary destructive actions on the host.
"""

from __future__ import annotations

import os
import sys
from typing import IO


SANDBOX_ENV_VARS: tuple[str, ...] = ("IS_SANDBOX", "CLAUDE_CODE_BUBBLEWRAP")


def _is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def is_sandbox_environment() -> bool:
    """True when an env var marks the process as running in a sandbox.

    The TS reference checks ``IS_SANDBOX === '1'`` and the truthy form of
    ``CLAUDE_CODE_BUBBLEWRAP``. We accept the same set of truthy values
    (``isEnvTruthy`` semantics).
    """
    return any(_is_truthy_env(os.environ.get(name)) for name in SANDBOX_ENV_VARS)


def _is_running_as_root() -> bool:
    if sys.platform == "win32":
        return False
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return False
    try:
        return getuid() == 0
    except OSError:  # pragma: no cover - extremely unlikely
        return False


def enforce_dangerous_skip_permissions_safety(
    *,
    bypass_requested: bool,
    stderr: IO[str] | None = None,
) -> None:
    """Refuse to start in bypass mode when running as root outside a sandbox.

    Mirrors the safety check in ``typescript/src/setup.ts``:

    * Skipped on Windows (no concept of root for this check).
    * Skipped if neither ``--dangerously-skip-permissions`` nor
      ``--allow-dangerously-skip-permissions`` was passed.
    * Skipped if the process is not running as uid 0.
    * Skipped when ``IS_SANDBOX`` or ``CLAUDE_CODE_BUBBLEWRAP`` is truthy.

    Otherwise prints the same error message used by the TS reference and
    raises :class:`SystemExit` with code 1 so callers can be unit-tested.
    """
    if not bypass_requested:
        return
    if not _is_running_as_root():
        return
    if is_sandbox_environment():
        return

    out = stderr if stderr is not None else sys.stderr
    out.write(
        "--dangerously-skip-permissions cannot be used with root/sudo "
        "privileges for security reasons\n"
    )
    try:
        out.flush()
    except Exception:  # pragma: no cover - flushing test buffers is best-effort
        pass
    raise SystemExit(1)
