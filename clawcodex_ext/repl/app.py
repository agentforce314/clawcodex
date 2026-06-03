"""Downstream-enhanced REPL (ClawCodexExtREPL).

Subclass of :class:`src.repl.core.ClawcodexREPL` that adds provider
injection, runtime-context awareness, soft-fallback for missing API
keys, session resume, thinking-toggle support, and the ``/provider``
slash command wiring — all without touching ``src/``.

Usage
-----

    from clawcodex_ext.repl.app import ClawCodexExtREPL

    repl = ClawCodexExtREPL(
        provider_name="glm",
        stream=False,
        permission_mode="default",
        is_bypass_permissions_mode_available=False,
        resume_session_id="abc123",
        provider=...,       # optional pre-built provider
        session=...,        # optional pre-built session
        tool_registry=...,
        tool_context=...,
        workspace_root=Path.cwd(),
        runtime_context=...,
    )
    repl.run()
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.agent import Session
from src.providers.runtime import build_provider_from_config
from src.repl.core import ClawcodexREPL

if TYPE_CHECKING:
    pass

from rich.console import Console as RichConsole


class ClawCodexExtREPL(ClawcodexREPL):
    """Downstream-enhanced REPL with provider injection and runtime context.

    Accepts all the same public-method interface as
    :class:`ClawcodexREPL` (``run``, ``handle_command``, ``chat``, …)
    but overrides ``__init__`` and ``_init_command_system`` to support
    downstream extensions.
    """

    def __init__(
        self,
        provider_name: str = "glm",
        stream: bool = False,
        *,
        permission_mode: str = "default",
        is_bypass_permissions_mode_available: bool = False,
        # Downstream-only parameters ------------------------------------
        resume_session_id: str | None = None,
        provider: Any | None = None,
        session: Session | None = None,
        tool_registry: Any | None = None,
        tool_context: Any | None = None,
        workspace_root: Path | None = None,
        runtime_context: Any | None = None,
    ) -> None:
        # ---- Shared setup (identical to upstream) ----
        self._permission_mode = permission_mode
        self._is_bypass_permissions_mode_available = bool(
            is_bypass_permissions_mode_available
        )

        from rich.console import Console

        self.console = Console()
        self.runtime_context = runtime_context
        self.provider_name = provider_name
        self.stream = stream
        self.workspace_root = workspace_root or Path.cwd()

        # ---- Provider construction (downstream) ----
        if provider is not None:
            self.provider = provider
            self._api_key_missing = False
        else:
            try:
                self.provider = build_provider_from_config(provider_name)
                self._api_key_missing = False
            except RuntimeError:
                self._api_key_missing = True

        if self._api_key_missing:
            # No configured credentials — initialise minimal read-only state
            self.provider = None
            self.session = None
            self.tool_registry = None
            self.tool_context = None
            self._engine_messages = []
            self._queued_prompts = []
            self._queued_prompts_lock = threading.Lock()
            self._original_built_ins = [
                "/", "/help", "/exit", "/quit", "/q", "/clear",
                "/save", "/load", "/stream", "/render-last", "/tools",
                "/tool", "/skills", "/init", "/tui", "/login",
            ]
            self._built_in_commands = list(self._original_built_ins)
            return

        # ---- Session: create or resume ----
        self._resume_session_id = resume_session_id
        if session is not None:
            self.session = session
        elif resume_session_id:
            loaded_session = Session.resume(resume_session_id)
            if loaded_session is not None:
                self.session = loaded_session
                self.console.print(
                    f"[green]Resumed session: {resume_session_id}[/green]"
                )
                self.console.print(
                    f"[dim]Provider: {loaded_session.provider}, "
                    f"Model: {loaded_session.model}[/dim]"
                )
                self._sync_conversation_from_transcript(resume_session_id)
            else:
                self.console.print(
                    f"[yellow]Session not found: {resume_session_id}. "
                    "Starting new session.[/yellow]"
                )
                self.session = Session.create(provider_name, self.provider.model)
        else:
            self.session = Session.create(provider_name, self.provider.model)

        # ---- Tool registry + context ----
        def _get_mcp_servers_for_prompt() -> list[str]:
            ctx = getattr(self, "tool_context", None)
            if ctx is None:
                return []
            clients = getattr(ctx, "mcp_clients", None) or {}
            return list(clients.keys())

        from src.tool_system.defaults import build_default_registry

        self.tool_registry = tool_registry or build_default_registry(
            provider=self.provider,
            get_available_mcp_servers=_get_mcp_servers_for_prompt,
        )
        self._engine_messages: list[Any] = []

        from src.permissions.types import ToolPermissionContext
        from src.tool_system.context import ToolContext

        if tool_context is None:
            self.tool_context = ToolContext(
                workspace_root=self.workspace_root,
                permission_context=ToolPermissionContext(
                    mode=self._permission_mode,  # type: ignore[arg-type]
                    is_bypass_permissions_mode_available=(
                        self._is_bypass_permissions_mode_available
                    ),
                ),
            )
        else:
            self.tool_context = tool_context
            self.tool_context.workspace_root = self.workspace_root
            self.tool_context.permission_context = ToolPermissionContext(
                mode=self._permission_mode,  # type: ignore[arg-type]
                is_bypass_permissions_mode_available=(
                    self._is_bypass_permissions_mode_available
                ),
            )
        self.tool_context.ask_user = self._ask_user_questions
        self._current_status = None
        if self._permission_mode == "bypassPermissions":
            self.tool_context.allow_docs = True
            self.tool_context.permission_handler = (
                lambda _tn, _msg, _sug: (True, False)
            )
        else:
            self.tool_context.permission_handler = self._handle_permission_request

        # ---- State fields (shared with upstream) ----
        self._stats_turns: int = 0
        self._stats_input_tokens: int = 0
        self._stats_output_tokens: int = 0
        self._direct_stream_abort: bool = False
        self._queued_prompts: list[str] = []
        self._queued_prompts_lock = threading.Lock()
        self._permission_prompt_lock = threading.Lock()
        self._permission_decision_cache: dict[str, bool] = {}
        self._active_live_status: Any = None
        self._expandable_blocks: deque[tuple[str, str]] = deque(maxlen=20)

        # ---- Downstream-only state ----
        self._thinking_visible: bool = True
        self._thinking_chunks: list[str] = []

        # ---- Cost tracker & history (created here for _init_command_system)
        from src.cost_tracker import CostTracker
        from src.history import HistoryLog

        self.cost_tracker = CostTracker()
        self.history_log = HistoryLog()

        # ---- Original built-in commands ----
        self._original_built_ins = [
            "/",
            "/help",
            "/exit",
            "/quit",
            "/q",
            "/repl",
            "/clear",
            "/save",
            "/load",
            "/stream",
            "/render-last",
            "/tools",
            "/tool",
            "/skills",
            "/init",
            "/model",
            "/provider",
            "/env",
            "/tui",
            "/login",
            "/permission",
        ]
        self._built_in_commands = list(self._original_built_ins)

    # ---- Override _init_command_system to pass downstream fields ----

    def _init_command_system(self) -> None:
        """Initialise the command system with downstream context."""
        from src.command_system import (
            CommandRegistry,
            create_command_context,
            register_builtin_commands,
        )

        register_builtin_commands(None)

        self.command_registry = CommandRegistry()
        register_builtin_commands(self.command_registry)

        self.command_context = create_command_context(
            workspace_root=self.workspace_root,
            conversation=self.session.conversation,
            cost_tracker=self.cost_tracker,
            history=self.history_log,
            provider=self.provider,
            tool_registry=self.tool_registry,
            tool_context=self.tool_context,
            runtime_context=self.runtime_context,
        )

        self._update_built_in_commands_with_command_system()
