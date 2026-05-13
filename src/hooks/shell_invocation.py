"""Per-hook shell selection — Chapter 12 round 2.

Command hooks can opt into PowerShell execution via the ``shell`` field on
each ``HookConfig``. Mirrors:

* ``typescript/src/utils/shell/shellProvider.ts:1-3`` — the ``SHELL_TYPES``
  enum and the ``DEFAULT_HOOK_SHELL`` constant.
* ``typescript/src/utils/shell/powershellProvider.ts:11-13`` —
  ``buildPowerShellArgs``: the canonical ``-NoProfile -NonInteractive
  -Command <cmd>`` flag set.

This module owns nothing about path conversion (Windows / Git Bash), profile
sourcing, or env propagation — those stay in ``hook_executor`` where the
subprocess actually spawns. The split keeps this file small enough to mock
cheaply in tests.
"""

from __future__ import annotations

import shutil
from typing import Literal

# Tuple, not frozenset — preserves iteration order so error messages list
# valid choices in TS-shipping order ("bash" first).
SHELL_TYPES: tuple[str, ...] = ("bash", "powershell")

ShellType = Literal["bash", "powershell"]

# The bash path is the historical default and the only one exercised on
# POSIX before this round. Matches ``DEFAULT_HOOK_SHELL`` in
# ``shellProvider.ts:3``. HookConfig.shell == None is treated as this
# default by the executor.
DEFAULT_HOOK_SHELL: ShellType = "bash"


def build_powershell_args(cmd: str) -> list[str]:
    """Return the argv list for spawning ``pwsh`` with ``cmd``.

    Mirrors ``buildPowerShellArgs`` in
    ``typescript/src/utils/shell/powershellProvider.ts:11-13``:

    * ``-NoProfile``     — skip user profile scripts (faster, deterministic).
    * ``-NonInteractive`` — fail fast instead of prompting for input.
    * ``-Command``       — execute the literal string that follows.

    The caller invokes ``asyncio.create_subprocess_exec(pwsh_path,
    *build_powershell_args(cmd), ...)`` — explicit argv with no intervening
    shell. This matches the TS ``spawn(pwshPath, buildPowerShellArgs(cmd),
    ...)`` call at ``typescript/src/utils/hooks.ts:1108``.
    """
    return ["-NoProfile", "-NonInteractive", "-Command", cmd]


def find_powershell_path() -> str | None:
    """Locate PowerShell on PATH.

    Preference order matches TS ``getCachedPowerShellPath`` semantics:

    1. ``pwsh`` — cross-platform PowerShell 6+. Present on macOS / Linux when
       installed via Homebrew / package manager; on Windows when installed
       from the Microsoft Store or MSI.
    2. ``powershell`` — Windows-only Windows-PowerShell 5.1. Always present
       on Windows; absent everywhere else.

    Returns ``None`` if neither executable is on PATH. The caller surfaces a
    deterministic blocking error to the hook author.

    No caching here — ``shutil.which`` is cheap (one filesystem walk per
    PATH entry) and tests rely on monkeypatching it. The TS version caches
    because spawn-per-hook adds up across a long session; we can add
    ``@functools.lru_cache`` later if profiling shows it matters.
    """
    return shutil.which("pwsh") or shutil.which("powershell")


__all__ = [
    "SHELL_TYPES",
    "ShellType",
    "DEFAULT_HOOK_SHELL",
    "build_powershell_args",
    "find_powershell_path",
]
