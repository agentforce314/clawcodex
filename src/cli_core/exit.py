"""CLI exit helpers.

Port of ``typescript/src/cli/exit.ts``. Centralizes the ``print + exit`` block
copy-pasted across subcommand handlers and gives callers a ``NoReturn`` type
so control-flow analysis narrows correctly after the call.
"""

from __future__ import annotations

import sys
from typing import NoReturn


def cli_error(msg: str | None = None, code: int = 1) -> NoReturn:
    """Write ``msg`` to stderr (if provided) and exit with ``code`` (default 1)."""

    if msg:
        print(msg, file=sys.stderr)
    sys.exit(code)


def cli_ok(msg: str | None = None) -> NoReturn:
    """Write ``msg`` to stdout (if provided) and exit with code 0."""

    if msg:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    sys.exit(0)
