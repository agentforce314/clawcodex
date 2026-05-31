"""Downstream RuntimeContext — unified provider/tool/session factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RuntimeOptions:
    """Options for building a RuntimeContext, merged from TUIOptions and HeadlessOptions."""

    provider_name: str | None = None
    model: str | None = None
    max_turns: int = 20
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    workspace_root: Path | None = None
    stream: bool = True
    permission_mode: str = "default"
    is_bypass_permissions_mode_available: bool = False
    skip_permissions: bool = False  # backward-compat alias for headless
    resume_session_id: str | None = None
    resume_browse: bool = False
    verbose: bool = False


@dataclass
class RuntimeContext:
    """Unified runtime context carrying provider, tool registry, tool context, and session.

    Produced by :meth:`RuntimeContext.build`. Consumed by all frontends
    (REPL, TUI, headless) to avoid duplicating provider/tool/session setup.
    """

    provider: Any
    provider_name: str
    tool_registry: Any
    tool_context: Any
    session: Any | None
    workspace_root: Path
    options: RuntimeOptions

    @classmethod
    def build(cls, options: RuntimeOptions) -> RuntimeContext:
        """Build a RuntimeContext from options.

        Unifies the provider/registry/context/session construction that was
        previously duplicated across headless.py, tui.py, and repl/core.py.
        """
        from src.agent.session import Session as AgentSession
        from src.config import get_default_provider
        from src.permissions.types import ToolPermissionContext
        from src.providers.runtime import build_provider_from_config
        from src.tool_system.context import ToolContext
        from src.tool_system.defaults import build_default_registry

        workspace_root = options.workspace_root or Path.cwd()

        # Resolve effective permission mode (handle skip_permissions alias)
        if options.skip_permissions:
            effective_mode = "bypassPermissions"
            bypass_available = True
        else:
            effective_mode = options.permission_mode
            bypass_available = options.is_bypass_permissions_mode_available

        # Build provider
        provider_name = options.provider_name or get_default_provider()
        provider = build_provider_from_config(provider_name, options.model)

        # Build tool registry
        tool_registry = build_default_registry(provider=provider)
        from clawcodex_ext.cron_system.runtime import replace_cron_tools

        replace_cron_tools(tool_registry)

        # Apply tool filtering
        _filter_registry(
            tool_registry,
            allowed=options.allowed_tools,
            denied=options.disallowed_tools,
        )

        # Build tool context
        tool_context = ToolContext(
            workspace_root=workspace_root,
            permission_context=ToolPermissionContext(
                mode=effective_mode,
                is_bypass_permissions_mode_available=bypass_available,
            ),
        )
        if effective_mode == "bypassPermissions":
            tool_context.allow_docs = True
        tool_context.options.is_non_interactive_session = False

        # Resume session if requested
        session = None
        if options.resume_session_id:
            session, _tail_follower = AgentSession.resume_with_tail(
                options.resume_session_id,
            )

        runtime = cls(
            provider=provider,
            provider_name=provider_name,
            tool_registry=tool_registry,
            tool_context=tool_context,
            session=session,
            workspace_root=workspace_root,
            options=options,
        )
        from clawcodex_ext.cron_system.runtime import attach_cron_runtime

        attach_cron_runtime(runtime)
        return runtime


def _filter_registry(
    registry,
    *,
    allowed: tuple[str, ...] = (),
    denied: tuple[str, ...] = (),
) -> None:
    """Filter tool registry by allowed/denied name sets.

    Moved from src/entrypoints/tui.py so RuntimeContext.build() can use it
    without importing from the TUI entrypoint.
    """
    names = [t.name for t in registry.list_tools()]
    for name in names:
        should_remove = False
        if allowed and name.lower() not in {n.lower() for n in allowed}:
            should_remove = True
        if denied and name.lower() in {n.lower() for n in denied}:
            should_remove = True
        if should_remove:
            try:
                registry.unregister(name)
            except Exception:
                try:
                    del registry._tools[name]  # type: ignore[attr-defined]
                except Exception:
                    pass