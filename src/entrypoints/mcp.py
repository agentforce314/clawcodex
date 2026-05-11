"""Fast-path handler for ``clawcodex mcp ...`` subcommands.

WI-4.3: invoked from ``src/cli.py``'s pre-argparse subcommand sieve so the
MCP tooling path doesn't load the TUI/REPL/full tool registry. Imports
only what it needs (the ``src.services.mcp`` package) and exits.

Mirrors the chapter's "fast-path dispatch" pattern (TS ``main.tsx:914+``):
specialized subcommands get an early-return that skips the React REPL.
"""

from __future__ import annotations

import sys


def run_mcp_subcommand(rest: list[str]) -> int:
    """Handle ``clawcodex mcp <verb> [args...]``.

    Verbs (initial set):
      * ``list``   — list configured MCP servers (reads
        ``~/.claude/settings.json``-style config and prints names).
      * ``--help`` — print usage and exit 0.

    Returns the process exit code.
    """
    if not rest or rest[0] in ("--help", "-h"):
        _print_usage()
        return 0

    verb = rest[0]
    if verb == "list":
        return _list_servers()
    print(f"clawcodex mcp: unknown verb {verb!r}", file=sys.stderr)
    _print_usage()
    return 2


def _print_usage() -> None:
    print("Usage: clawcodex mcp <verb> [args...]")
    print("")
    print("Verbs:")
    print("  list    List configured MCP servers")


def _list_servers() -> int:
    """Print each configured MCP server's name on its own line.

    Avoids importing the TUI/REPL/full tool registry — only loads what
    the MCP config layer needs. The chapter's fast-path test (skip the
    React REPL on ``claude mcp``) is verified by ensuring this handler
    does NOT trigger ``src.tui.app`` or ``src.repl.core`` imports.
    """
    try:
        # Local imports keep the module-load cost off the hot cold-start
        # path of the interactive CLI. ``get_all_mcp_configs`` returns
        # ``(dict[str, ScopedMcpServerConfig], list[ValidationError])``.
        from src.services.mcp.config import get_all_mcp_configs
    except Exception as exc:  # pragma: no cover
        print(f"clawcodex mcp list: cannot load MCP config: {exc}", file=sys.stderr)
        return 1
    try:
        configs, _errors = get_all_mcp_configs()
    except Exception as exc:
        print(f"clawcodex mcp list: error reading config: {exc}", file=sys.stderr)
        return 1
    names = sorted(configs.keys())
    if not names:
        print("(no MCP servers configured)")
        return 0
    for name in names:
        print(name)
    return 0
