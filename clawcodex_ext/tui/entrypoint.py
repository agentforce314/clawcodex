"""Downstream TUI entrypoint — extended version with session resume support.

Called by :class:`clawcodex_ext.frontend.tui.TUIFrontend.run` which passes
the full runtime context (provider, session, tool registry, etc.) so the
TUI can resume an existing session and print a resume hint on exit.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from src.agent import Session
from src.entrypoints.tui import (
    TUIOptions,
    _replay_transcript_to_host,
    _textual_available,
)
from src.tool_system.context import ToolContext
from src.tool_system.registry import ToolRegistry

from clawcodex_ext.tui.app import ClawCodexTUI


def run_tui(
    options: TUIOptions,
    *,
    provider: Any | None = None,
    session: Session | None = None,
    tool_registry: ToolRegistry | None = None,
    tool_context: ToolContext | None = None,
    runtime_context: Any | None = None,
    resume_session_id: str | None = None,
    resume_browse: bool = False,
    append_system_prompt: str = "",
) -> int:
    """Boot the Textual TUI with extended options and print resume hint on exit.

    Args:
        options: Base TUI options (provider name, model, permissions, …).
        provider: Pre-built provider instance (optional).
        session: Pre-built session to use (optional).
        tool_registry: Pre-built tool registry (optional).
        tool_context: Pre-built tool context (optional).
        runtime_context: RuntimeContext from CLI dispatch (optional).
        resume_session_id: Session ID to resume (optional).
        resume_browse: If True, show the session browser on startup (default False).
        append_system_prompt: Extra system prompt text to append.

    Returns:
        CLI exit code (0 on success, 130 on KeyboardInterrupt).
    """
    if not _textual_available():
        from src.cli_core.exit import cli_error

        cli_error(
            "error: textual is not installed. "
            "Install it with `pip install 'textual>=0.79'` or pass --no-tui.",
            2,
        )

    workspace_root = options.workspace_root or Path.cwd()

    # Build provider if not injected
    if provider is None:
        from src.config import get_default_provider, get_provider_config
        from src.providers import get_provider_class

        provider_name = options.provider_name or get_default_provider()
        try:
            provider_cfg = get_provider_config(provider_name)
        except Exception as exc:
            from src.cli_core.exit import cli_error

            cli_error(f"error: unable to load provider config: {exc}", 2)
        if not provider_cfg.get("api_key"):
            from src.cli_core.exit import cli_error

            cli_error(
                f"error: API key for provider '{provider_name}' is not configured. "
                "Run `clawcodex login` to set it up.",
                2,
            )
        provider_cls = get_provider_class(provider_name)
        model = options.model or provider_cfg.get("default_model")
        provider = provider_cls(
            api_key=provider_cfg["api_key"],
            base_url=provider_cfg.get("base_url"),
            model=model,
        )
    else:
        provider_name = options.provider_name or getattr(
            provider, "provider_name", "unknown"
        )

    # Build tool registry + context if not injected
    if tool_registry is None:
        from src.tool_system.defaults import build_default_registry

        tool_registry = build_default_registry(provider=provider)
        if options.allowed_tools:
            allow = {name.lower() for name in options.allowed_tools}
            _filter_registry(tool_registry, keep=lambda n: n.lower() in allow)
        if options.disallowed_tools:
            deny = {name.lower() for name in options.disallowed_tools}
            _filter_registry(tool_registry, keep=lambda n: n.lower() not in deny)

    if tool_context is None:
        from src.permissions.types import ToolPermissionContext

        tool_context = ToolContext(
            workspace_root=workspace_root,
            permission_context=ToolPermissionContext(
                mode=options.permission_mode or "default",
                is_bypass_permissions_mode_available=bool(
                    options.is_bypass_permissions_mode_available
                ),
            ),
        )
        if options.permission_mode == "bypassPermissions":
            tool_context.allow_docs = True
        tool_context.options.is_non_interactive_session = False

    model_label = getattr(provider, "model", "")

    # Build session: resume or create
    if session is not None:
        used_session = session
    elif resume_session_id:
        loaded = Session.resume(resume_session_id)
        if loaded is not None:
            used_session = loaded
        else:
            from rich.console import Console

            console = Console()
            console.print(
                f"[yellow]Session not found: {resume_session_id}. "
                "Starting new session.[/yellow]"
            )
            used_session = Session.create(provider_name, model_label)
    else:
        used_session = Session.create(provider_name, model_label)

    # Build and run app
    app = ClawCodexTUI(
        provider=provider,
        provider_name=provider_name,
        workspace_root=workspace_root,
        tool_registry=tool_registry,
        tool_context=tool_context,
        session=used_session,
        max_turns=options.max_turns,
        stream=options.stream,
        resume_browse=resume_browse,
        runtime_context=runtime_context,
        append_system_prompt=append_system_prompt,
    )

    # ---- SIGTERM/SIGINT: save session + print resume hint via graceful shutdown (S-R1) ----
    _register_tui_signal_save(used_session)

    try:
        app.run(inline=True, inline_no_clear=True, mouse=False)
    except KeyboardInterrupt:
        return 130

    # --- After TUI exits, print resume hint ---
    _print_resume_hint(used_session)
    return 0


def _register_tui_signal_save(session: Session) -> None:
    """Register graceful-shutdown cleanup that saves the TUI session and
    prints a resume hint when the process receives SIGTERM/SIGINT."""
    try:
        from src.utils.graceful_shutdown import register_cleanup
    except ImportError:
        return

    ref = {"printed": False}

    def _cleanup() -> None:
        if ref["printed"]:
            return
        ref["printed"] = True
        # Save session state
        try:
            session.save()
        except Exception:
            pass
        # Print resume hint
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


def _print_resume_hint(session: Session) -> None:
    """Print resume hint to the host terminal after TUI teardown.

    Matches CCB's ``printResumeHint()`` behaviour: only print when the
    host stdout is a TTY and the session has a valid ID.
    """
    if not sys.stdout.isatty():
        return
    sid = getattr(session, "session_id", None) or ""
    if not sid:
        return
    from rich.console import Console

    console = Console()
    console.print(f"\n[dim]Resume this session with: clawcodex --resume {sid}[/dim]")


def _filter_registry(registry, *, keep) -> None:
    """Filter tools in a registry by a predicate."""
    names = [t.name for t in registry.list_tools()]
    for name in names:
        if not keep(name):
            try:
                registry.unregister(name)
            except Exception:
                try:
                    del registry._tools[name]
                except Exception:
                    pass


__all__ = ["run_tui"]
