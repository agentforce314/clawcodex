"""Downstream RuntimeContext — unified provider/tool/session factory."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clawcodex_ext.cli.model_cmd.resolver import resolve
from clawcodex_ext.cron_system.runtime import attach_cron_runtime, replace_cron_tools
from clawcodex_ext.runtime.observer import (
    RuntimeObserver,
    attach_observer,
    detach_observer,
    notify_observers,
)


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
    fork_session_id: str | None = None
    resume_session_at: int | None = None  # S-R4-AT: message index to resume at
    verbose: bool = False
    append_system_prompt: str = ""
    agent_dir_override: Path | None = None


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
        resolution = resolve(
            cli_provider=options.provider_name,
            cli_model=options.model,
            project_root=workspace_root,
        )
        provider_name = resolution.provider
        provider = build_provider_from_config(provider_name, resolution.model)
        options.provider_name = provider_name
        options.model = resolution.model

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

        # Wire persistent cron scheduler to the tool context (F-22).
        # Runs a background daemon thread that checks for due tasks
        # every second and pushes cron_prompt events to the outbox.
        attach_cron_runtime(tool_context, autostart=True)

        # Resume session if requested
        session = None
        if options.resume_session_id:
            from clawcodex_ext.agent.session_ext import resume_session_with_tail
            session, _tail_follower = resume_session_with_tail(
                options.resume_session_id,
            )

        # Fork session: load existing history into a new session (S-R4-F)
        if options.fork_session_id and not options.resume_session_id:
            from src.agent import Session as AgentSession
            old_session = AgentSession.resume(options.fork_session_id)
            if old_session is not None:
                # Create a brand new session
                new_session = AgentSession.create(
                    provider_name,
                    options.model or getattr(provider, "model", ""),
                )
                # Copy conversation messages from old session
                if old_session.conversation and old_session.conversation.messages:
                    new_session.conversation.messages = list(
                        old_session.conversation.messages
                    )
                session = new_session

        # S-R4-AT: truncate conversation to a specific message index
        if session is not None and options.resume_session_at is not None:
            idx = options.resume_session_at
            if session.conversation and session.conversation.messages:
                total = len(session.conversation.messages)
                if 0 <= idx < total:
                    session.conversation.messages = session.conversation.messages[:idx + 1]

        runtime = cls(
            provider=provider,
            provider_name=provider_name,
            tool_registry=tool_registry,
            tool_context=tool_context,
            session=session,
            workspace_root=workspace_root,
            options=options,
        )
        attach_cron_runtime(runtime)
        return runtime

    def swap_provider(self, provider_name: str, model: str | None = None) -> None:
        from clawcodex_ext.cli.model_cmd.registry import ModelRegistry
        from clawcodex_ext.cli.model_cmd.errors import (
            ProviderMismatchError,
            UnknownModelError,
        )
        from clawcodex_ext.cli.provider_cmd.errors import UnknownProviderError
        from src.providers.runtime import build_provider_from_config
        from src.tool_system.defaults import build_default_registry

        registry = ModelRegistry()
        try:
            registry.validate_provider(provider_name)
        except UnknownProviderError:
            import sys
            print(
                f"Warning: provider '{provider_name}' is not in the built-in list — "
                f"proceeding anyway",
                file=sys.stderr,
            )
        if model is not None:
            try:
                registry.validate_model(model, provider_name)
            except (UnknownModelError, ProviderMismatchError):
                pass  # Unknown model on unknown provider is fine

        provider = build_provider_from_config(provider_name, model)
        tool_registry = build_default_registry(provider=provider)
        replace_cron_tools(tool_registry)
        _filter_registry(
            tool_registry,
            allowed=self.options.allowed_tools,
            denied=self.options.disallowed_tools,
        )

        self.provider = provider
        self.provider_name = provider_name
        self.tool_registry = tool_registry
        self.options.provider_name = provider_name
        self.options.model = getattr(provider, "model", model)

        for attr, value in (
            ("provider", provider),
            ("provider_name", provider_name),
            ("tool_registry", tool_registry),
        ):
            if hasattr(self.tool_context, attr):
                setattr(self.tool_context, attr, value)

        # Fan-out to downstream observers (REPL, TUI, AgentBridge).
        # See clawcodex_ext/runtime/observer.py for the Protocol contract.
        notify_observers(self)


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