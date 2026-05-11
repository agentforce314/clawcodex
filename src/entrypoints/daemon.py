"""Fast-path handler for ``clawcodex daemon``.

WI-4.3: ``daemon`` is a placeholder for a future background-runtime
subcommand (the TS port has it for the long-running coordinator host).
Today it's a stub that prints a "not implemented" message and exits.

Importantly, the stub keeps the cold-start path light — it does NOT
trigger the TUI/REPL imports.
"""

from __future__ import annotations

import sys


def run_daemon_subcommand(rest: list[str]) -> int:
    """Print a "not yet implemented" message and exit cleanly.

    The handler exists primarily so ``clawcodex daemon`` doesn't fall
    through to the interactive REPL bootstrap (which would load Textual
    and the full tool registry — wasteful for a non-interactive command).
    """
    print(
        "clawcodex daemon: not yet implemented in this Python port. "
        "See the TypeScript reference at typescript/src/coordinator/.",
        file=sys.stderr,
    )
    return 1
