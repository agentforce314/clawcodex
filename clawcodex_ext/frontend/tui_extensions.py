"""F-43 extension hook for the TUI frontend.

Mirror of :mod:`clawcodex_ext.frontend.repl_extensions` for the Textual
TUI. Owns the F-43 ``/provider`` and ``/model`` slash command
registration plus the runtime observer that syncs the TUI's
``app_state`` / ``AgentBridge`` private state after
:meth:`RuntimeContext.swap_provider`.

Why this is in downstream
-------------------------
The TUI lives at ``src/tui/*`` and the AgentBridge inside
``src/tui/agent_bridge.py`` holds private references to
``provider`` / ``tool_registry`` / ``tool_context`` that need to be
refreshed on provider swap. By keeping the observer in downstream, the
upstream TUI only needs to expose an ``_agent_bridge`` attribute and a
``status_bar`` widget; all F-43 logic stays in
``clawcodex_ext/frontend/tui_extensions.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from clawcodex_ext.cli.runtime_commands import register_runtime_commands
from clawcodex_ext.runtime.observer import RuntimeObserver, attach_observer

if TYPE_CHECKING:  # pragma: no cover
    from src.tui.app import ClawCodexTUI

_log = logging.getLogger(__name__)


class _TuiRuntimeObserver:
    """Sync TUI private state when the runtime swaps provider.

    The TUI's :class:`AgentBridge` keeps private copies of the provider
    / tool registry / tool context so background workers can dispatch
    without going through the app. After a provider swap those copies
    must be replaced; we delegate to the bridge's
    :meth:`AgentBridge.replace_runtime` for the heavy lifting and then
    refresh the visible status bar / ``app_state`` mirror.
    """

    def __init__(self, app: "ClawCodexTUI") -> None:
        self._app = app

    def on_runtime_swap(self, runtime) -> None:
        app = self._app
        app.provider = runtime.provider
        app.provider_name = runtime.provider_name
        app.model = getattr(runtime.provider, "model", runtime.options.model)
        app.tool_registry = runtime.tool_registry
        app.tool_context = runtime.tool_context

        bridge = getattr(app, "_agent_bridge", None)
        if bridge is not None and hasattr(bridge, "replace_runtime"):
            bridge.replace_runtime(
                provider=runtime.provider,
                tool_registry=runtime.tool_registry,
                tool_context=runtime.tool_context,
            )

        state = getattr(app, "app_state", None)
        if state is not None:
            state.provider = app.provider_name
            state.model = app.model or ""

        if getattr(app, "_command_context", None) is not None:
            app._command_context.provider = app.provider
            app._command_context.tool_registry = app.tool_registry
            app._command_context.tool_context = app.tool_context

        repl_screen = getattr(app, "_repl_screen", None)
        if repl_screen is not None and hasattr(repl_screen, "status_bar"):
            try:
                repl_screen.status_bar.refresh_identity(
                    provider=app.provider_name,
                    model=app.model,
                )
            except Exception:
                pass


def install_tui_extensions(app: "ClawCodexTUI", ctx) -> None:
    """Wire F-43 slash commands + observer into the TUI.

    Registers ``/provider`` and ``/model`` into both the local REPL
    registry (for the REPL screen) and the global command registry
    (for ``dispatch_registry_command``). Also installs a
    :class:`RuntimeObserver` on the runtime so provider swaps in the
    TUI stay in sync with the bridge.

    Args:
        app: A fully-constructed :class:`ClawCodexTUI`. Reads
            ``app.runtime_context`` to find the runtime to attach to.
        ctx: The downstream :class:`RuntimeContext` (or any object
            exposing the runtime protocol). Used as a fallback when
            ``app.runtime_context`` is ``None``.
    """
    register_runtime_commands(None)  # global registry for async dispatch

    command_registry = getattr(app, "command_registry", None)
    if command_registry is not None:
        register_runtime_commands(command_registry)

    runtime = getattr(app, "runtime_context", None)
    if runtime is None:
        runtime = ctx
    if runtime is None:
        return

    attach_observer(runtime, _TuiRuntimeObserver(app))
