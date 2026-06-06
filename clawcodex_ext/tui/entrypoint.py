"""Downstream TUI entrypoint.

Provides a ``run_tui`` function that delegates to
:func:`_run_tui_with_app` with the downstream ``ClawCodexExtTUI`` app
class.  The helper also accepts downstream-specific fields (pre-built
provider, session, runtime-context, resume mode, etc.) as keyword
arguments.

Usage from a frontend plugin::

    from src.entrypoints.tui import TUIOptions
    from clawcodex_ext.tui.entrypoint import run_tui

    options = TUIOptions(provider_name=..., ...)
    return run_tui(
        options,
        provider=ctx.provider,
        session=ctx.session,
        tool_registry=ctx.tool_registry,
        tool_context=ctx.tool_context,
        workspace_root=ctx.workspace_root,
        runtime_context=ctx,
        resume_session_id=ctx.options.resume_session_id,
        resume_browse=ctx.options.resume_browse,
    )
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.cli_core.exit import cli_error
from src.config import get_default_provider, get_provider_config
from src.providers import get_provider_class
from src.entrypoints.tui import TUIOptions

from clawcodex_ext.tui.app import ClawCodexExtTUI

if TYPE_CHECKING:
    from src.agent import Session
    from src.tool_system.context import ToolContext


def run_tui(
    options: TUIOptions,
    *,
    provider: Any = None,
    session: Any = None,
    tool_registry: Any = None,
    tool_context: Any = None,
    workspace_root: Path | None = None,
    runtime_context: Any = None,
    resume_session_id: str | None = None,
    resume_browse: bool = False,
    tail_follower: Any = None,
) -> int:
    """Boot the downstream-owned Textual TUI.

    Args:
        options: Upstream ``TUIOptions`` (provider_name, model, …).
        provider: Optional pre-built provider instance.
        session: Optional pre-built session.
        tool_registry: Optional pre-built tool registry.
        tool_context: Optional pre-built tool context.
        workspace_root: Override for ``options.workspace_root``.
        runtime_context: Downstream runtime context object.
        resume_session_id: Session ID to resume.
        resume_browse: If True, show the session browser on startup.
    """
    return _run_tui_with_app(
        options,
        app_cls=ClawCodexExtTUI,
        provider=provider,
        session=session,
        tool_registry=tool_registry,
        tool_context=tool_context,
        workspace_root=workspace_root or options.workspace_root,
        runtime_context=runtime_context,
        resume_session_id=resume_session_id,
        resume_browse=resume_browse,
        tail_follower=tail_follower,
    )


def _run_tui_with_app(
    options: TUIOptions,
    *,
    app_cls=ClawCodexExtTUI,
    provider: Any = None,
    session: Any = None,
    tool_registry: Any = None,
    tool_context: Any = None,
    workspace_root: Path | None = None,
    runtime_context: Any = None,
    resume_session_id: str | None = None,
    resume_browse: bool = False,
    tail_follower: Any = None,
) -> int:
    """Build a TUI app instance of *app_cls* and run it.

    Mirrors the upstream ``run_tui`` logic while adding downstream
    injection seams (pre-built provider, session, runtime context).
    """
    from src.tool_system.context import ToolContext
    from src.tool_system.defaults import build_default_registry
    from src.permissions.types import ToolPermissionContext
    from src.agent import Session

    effective_workspace = workspace_root or options.workspace_root or Path.cwd()

    # ---- Provider ----
    if provider is not None:
        _provider = provider
        provider_name = options.provider_name or getattr(
            provider, "provider_name", "unknown"
        )
    else:
        provider_name = options.provider_name or get_default_provider()
        try:
            provider_cfg = get_provider_config(provider_name)
        except Exception as exc:
            cli_error(f"error: unable to load provider config: {exc}", 2)
        if not provider_cfg.get("api_key"):
            cli_error(
                f"error: API key for provider '{provider_name}' is not configured. "
                "Run `clawcodex login` to set it up.",
                2,
            )
        provider_cls = get_provider_class(provider_name)
        model = options.model or provider_cfg.get("default_model")
        _provider = provider_cls(
            api_key=provider_cfg["api_key"],
            base_url=provider_cfg.get("base_url"),
            model=model,
        )

    # ---- Tool registry ----
    _tool_registry = tool_registry or build_default_registry(provider=_provider)
    if options.allowed_tools:
        allow = {name.lower() for name in options.allowed_tools}
        _filter_registry(_tool_registry, keep=lambda n: n.lower() in allow)
    if options.disallowed_tools:
        deny = {name.lower() for name in options.disallowed_tools}
        _filter_registry(_tool_registry, keep=lambda n: n.lower() not in deny)

    # ---- Tool context ----
    if tool_context is not None:
        _tool_context = tool_context
        # Ensure workspace_root matches
        _tool_context.workspace_root = effective_workspace
        _tool_context.permission_context = ToolPermissionContext(
            mode=options.permission_mode or "default",
            is_bypass_permissions_mode_available=bool(
                options.is_bypass_permissions_mode_available
            ),
        )
    else:
        _tool_context = ToolContext(
            workspace_root=effective_workspace,
            permission_context=ToolPermissionContext(
                mode=options.permission_mode or "default",
                is_bypass_permissions_mode_available=bool(
                    options.is_bypass_permissions_mode_available
                ),
            ),
        )
    if options.permission_mode == "bypassPermissions":
        _tool_context.allow_docs = True
    _tool_context.options.is_non_interactive_session = False

    # ---- Session ----
    if session is not None:
        _session = session
    elif resume_session_id:
        loaded = Session.resume(resume_session_id)
        if loaded is not None:
            _session = loaded
        else:
            _session = Session.create(provider_name, getattr(_provider, "model", ""))
    else:
        _session = Session.create(provider_name, getattr(_provider, "model", ""))

    # ---- Build app ----
    app = app_cls(
        provider=_provider,
        provider_name=provider_name,
        workspace_root=effective_workspace,
        tool_registry=_tool_registry,
        tool_context=_tool_context,
        session=_session,
        max_turns=options.max_turns,
        stream=options.stream,
        runtime_context=runtime_context,
        tail_follower=tail_follower,
        resume_browse=resume_browse,
        append_system_prompt=options.append_system_prompt,
    )

    try:
        app.run(inline=True, inline_no_clear=True, mouse=False)
    except KeyboardInterrupt:
        return 130

    # Downstream exit-code dispatch -----------------------------------------
    exit_code = getattr(app, "_exit_code", 0)

    # __REPL__ exit — the user wants to switch from the TUI to the REPL.
    # Return a sentinel so the caller can catch it and launch the REPL
    # instead of exiting the process.
    if getattr(app, "_REPL_EXIT", False):
        from src.cli_core.exit import REPL_EXIT_CODE
        return REPL_EXIT_CODE

    return exit_code or 0


def _filter_registry(registry, *, keep) -> None:
    """Remove tools from *registry* whose name doesn't satisfy *keep*."""
    names = [t.name for t in registry.list_tools()]
    for name in names:
        if not keep(name):
            try:
                registry.unregister(name)
            except Exception:
                try:
                    del registry._tools[name]  # type: ignore[attr-defined]
                except Exception:
                    pass


__all__ = ["TUIOptions", "run_tui", "_run_tui_with_app"]
