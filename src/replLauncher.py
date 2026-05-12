"""REPL launcher — chapter phase 4 dispatch.

Mirrors ``typescript/src/replLauncher.tsx``. The chapter's framing:
"seven different code paths converge on replLauncher.tsx", though as
noted in the gap analysis the TS reality is a print/headless ↔
interactive-REPL split (TS actually splits print mode through
``runHeadless`` separately from ``launchRepl``).

The Python launcher mirrors that pragmatic split:

* :func:`launch_repl(args)` is the single unified entry point that
  ``cli.main()`` calls. It inspects ``args`` and dispatches to one of
  three concrete mode runners (print, TUI, REPL).
* The previous per-mode helpers in ``cli.py`` (``_run_print_mode``,
  ``_run_tui_mode``, ``start_repl``) are kept but become thin wrappers
  that ``launch_repl`` delegates to. This decouples the dispatch
  decision from the dispatch wiring.

Plan reference: ``my-docs/ch02-bootstrap-refactoring-plan.md`` Phase 3.

Deferred to later plan phases:
- ``--resume`` / ``--continue`` paths (session restore).
- SDK mode (Agent SDK harness).
- Pipe mode (stdin-only stream-json).
These would each add a branch to :func:`launch_repl`; the chapter's
"seven paths" total is achievable once they land.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = [
    "build_repl_banner",
    "launch_repl",
]


def build_repl_banner() -> str:
    """One-line banner used by tests to confirm the module loads."""

    return (
        "ClawCodex REPL (legacy prompt_toolkit + rich is default; "
        "Textual TUI opt-in via --tui or /tui)."
    )


def launch_repl(args: Any) -> int:
    """Chapter phase 4 launcher. Inspects ``args`` and dispatches to
    the right mode runner.

    Modes recognized (matches the cli.py argparse surface):

    * ``args.print`` → headless / print mode (`_dispatch_print`).
    * ``args.tui`` or auto-detected TUI environment → Textual UI
      (`_dispatch_tui`).
    * else → legacy ``prompt_toolkit`` + Rich REPL (`_dispatch_repl`).

    All three branches emit ``profile_checkpoint("phase4_dispatch")``
    before delegating, so the chapter's phase-4 boundary is observable
    regardless of which mode wins.

    **Precondition for the REPL branch:** ``args`` must have
    ``_resolved_permission_mode`` and ``_resolved_is_bypass_available``
    populated. ``cli._resolve_permission_state(args)`` is the canonical
    populator and is called from ``cli.main()`` before this function.
    The print and TUI branches read their own permission state from
    ``args.permission_mode`` and friends (via the per-mode helpers'
    option translation in ``_run_print_mode`` / ``_run_tui_mode``).
    Callers invoking ``launch_repl`` from harnesses outside
    ``cli.main()`` (future SDK shims, test rigs) must set these fields
    before delegating to the REPL branch.

    Returns a conventional CLI exit code.
    """
    from src.utils.startup_profiler import profile_checkpoint

    if getattr(args, "print", False):
        profile_checkpoint("mode_dispatch_print")
        profile_checkpoint("phase4_dispatch")
        return _dispatch_print(args)

    explicit_tui: bool | None = None
    if getattr(args, "tui", False):
        explicit_tui = True
    elif getattr(args, "legacy_repl", False) or getattr(args, "no_tui", False):
        explicit_tui = False

    from src.entrypoints.tui import should_use_tui

    if should_use_tui(explicit_tui):
        profile_checkpoint("mode_dispatch_tui")
        profile_checkpoint("phase4_dispatch")
        return _dispatch_tui(args)

    profile_checkpoint("mode_dispatch_repl")
    profile_checkpoint("phase4_dispatch")
    return _dispatch_repl(args)


def _dispatch_print(args: Any) -> int:
    """Dispatch to headless / print mode. Delegates to
    ``cli._run_print_mode`` to avoid duplicating the option-validation
    logic (which is dense and shared by other CLI entry points)."""
    from src.cli import _run_print_mode
    return _run_print_mode(args)


def _dispatch_tui(args: Any) -> int:
    """Dispatch to the Textual TUI. Delegates to ``cli._run_tui_mode``."""
    from src.cli import _run_tui_mode
    return _run_tui_mode(args)


def _dispatch_repl(args: Any) -> int:
    """Dispatch to the legacy prompt_toolkit + Rich REPL."""
    from src.cli import start_repl
    return start_repl(
        stream=getattr(args, "stream", False),
        permission_mode=args._resolved_permission_mode,
        is_bypass_permissions_mode_available=(
            args._resolved_is_bypass_available
        ),
    )
