"""F-43 extension hook for the REPL frontend.

This module owns the downstream side of the F-43 ``/provider`` and
``/model`` slash command wiring for :class:`src.repl.core.ClawcodexREPL`.
The goal is to keep all F-43 knowledge in ``clawcodex_ext/`` so the
upstream-shaped REPL core (``src/repl/core.py``) only sees a thin seam
(``runtime_context`` field + observer notification on swap).

Responsibilities
----------------
1. Register the F-43 ``/provider`` and ``/model`` ``LocalCommand``
   objects on the REPL's command registry.
2. Install a :class:`RuntimeObserver` that syncs the REPL's private
   ``provider`` / ``tool_registry`` / ``tool_context`` references after
   a :meth:`RuntimeContext.swap_provider` rebuild.

The frontend plugin (:class:`clawcodex_ext.frontend.repl.REPLFrontend`)
calls :func:`install_repl_extensions` immediately after
``ClawcodexREPL(...)`` construction but before ``repl.run()``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from clawcodex_ext.cli.runtime_commands import register_runtime_commands
from clawcodex_ext.runtime.observer import RuntimeObserver, attach_observer

if TYPE_CHECKING:  # pragma: no cover
    from src.repl.core import ClawcodexREPL

_log = logging.getLogger(__name__)


class _ReplRuntimeObserver:
    """Sync REPL private state when the runtime swaps provider.

    Implements :class:`RuntimeObserver`. The REPL holds cached
    references to ``provider`` / ``tool_registry`` / ``tool_context`` and
    a command context that mirrors them; all four must be refreshed
    after a provider swap so the next prompt uses the new model.
    """

    def __init__(self, repl: "ClawcodexREPL") -> None:
        self._repl = repl

    def on_runtime_swap(self, runtime) -> None:
        repl = self._repl
        repl.provider = runtime.provider
        repl.provider_name = runtime.provider_name
        repl.tool_registry = runtime.tool_registry
        repl.tool_context = runtime.tool_context
        if hasattr(repl, "command_context") and repl.command_context is not None:
            repl.command_context.provider = runtime.provider
            repl.command_context.tool_registry = runtime.tool_registry
            repl.command_context.tool_context = runtime.tool_context


def install_repl_extensions(repl: "ClawcodexREPL", ctx) -> None:
    """Wire F-43 slash commands + observer into the REPL.

    Args:
        repl: A fully-constructed :class:`ClawcodexREPL`. The function
            reads ``repl.command_registry`` and ``repl.runtime_context``;
            it does not mutate the REPL's public surface beyond
            registering commands and attaching an observer.
        ctx: The downstream :class:`RuntimeContext` (or any object
            exposing the runtime protocol). Used to attach the observer
            that fires on ``swap_provider``.
    """
    # Register /provider and /model into the REPL's local command
    # registry so the slash-command dispatcher can find them.
    if getattr(repl, "command_registry", None) is not None:
        register_runtime_commands(repl.command_registry)

    runtime = getattr(repl, "runtime_context", None)
    if runtime is None:
        runtime = ctx
    if runtime is None:
        return

    attach_observer(runtime, _ReplRuntimeObserver(repl))

    # ---- SIGTERM / SIGINT: save session + print resume hint (S-R1) ----
    _register_signal_session_save(repl)


def _register_signal_session_save(repl: "ClawcodexREPL") -> None:
    """Register a graceful-shutdown cleanup that saves the session and
    prints a resume hint when the process receives SIGTERM/SIGINT.

    Uses the upstream ``register_cleanup`` from ``src.utils.graceful_shutdown``
    which is already installed by ``init()``.
    """
    try:
        from src.utils.graceful_shutdown import register_cleanup
    except ImportError:
        return

    # Capture session reference once at registration time.
    sid_ref = {  # mutable container so the closure can re-read .session_id
        "session": None,
        "printed": False,
    }

    def _capture_ref() -> None:
        sid_ref["session"] = getattr(repl, "session", None)

    # Snapshot the session now and also just before the cleanup runs.
    _capture_ref()

    def _cleanup() -> None:
        if sid_ref["printed"]:
            return
        sid_ref["printed"] = True
        _capture_ref()
        session = sid_ref["session"]
        if session is None:
            return
        # Save session state
        try:
            session.save()
        except Exception:
            pass
        # Print resume hint (only if stdout is a TTY)
        sid = getattr(session, "session_id", None) or ""
        if not sid:
            return
        try:
            import sys
            if sys.stdout.isatty():
                from rich.console import Console
                Console().print(
                    f"\n[dim]Resume this session with: clawcodex --resume {sid}[/dim]"
                )
        except Exception:
            pass

    register_cleanup(_cleanup)
