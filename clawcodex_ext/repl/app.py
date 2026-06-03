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
from src.repl.core import ClawcodexREPL, _SlashOnlyCompleter

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

            # Minimal prompt session for the missing-key case so that
            # run() doesn't crash on ``self.prompt_session.prompt()``.
            from prompt_toolkit import PromptSession as _P
            self.prompt_session = _P()  # type: ignore[call-arg]
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

        # ---- Initialise command system (must happen before PromptSession) ----
        # NOTE: this is deferred to first use of _init_command_system
        # via the _init_command_system override below; we call it here
        # to match upstream ordering.
        self._init_command_system()

        # ---- Prompt toolkit (from upstream __init__ lines 529-713) ----
        from pathlib import Path as _Path

        from prompt_toolkit.completion import merge_completers
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.styles import Style

        from src.repl.at_file_completer import AtFileCompleter

        history_file = _Path.home() / ".clawcodex" / "history"
        history_file.parent.mkdir(parents=True, exist_ok=True)

        # TTL cache for the slash-command suggestion list.
        self._slash_suggestions_cache: list[Any] | None = None
        self._slash_suggestions_cache_at: float = 0.0

        self._slash_completer = _SlashOnlyCompleter(
            self._get_slash_command_words,
            suggestions_provider=self._get_slash_command_suggestions,
        )
        self._at_completer = AtFileCompleter(
            cwd=str(self.tool_context.workspace_root)
        )
        self.completer = merge_completers(
            [self._slash_completer, self._at_completer]
        )

        # Warm the slash-command suggestion cache in the background.
        threading.Thread(
            target=self._warm_slash_suggestions_cache,
            name="slash-suggestions-warm",
            daemon=True,
        ).start()

        # ---- Key bindings (from upstream __init__ lines 595-682) ----
        from prompt_toolkit.key_binding import KeyBindings

        self.bindings = KeyBindings()
        if hasattr(self.bindings, "add"):

            @self.bindings.add("/")
            def _show_slash_completions(event):
                buf = event.current_buffer
                was_empty = buf.text == ""
                buf.insert_text("/")
                if was_empty:
                    buf.start_completion(select_first=False)

            def _refresh_slash_menu_after_deletion(event, deleter):
                buf = event.current_buffer
                deleter(buf)
                if not (buf.completer and buf.complete_while_typing()):
                    return
                token, _ = _SlashOnlyCompleter._current_slash_token(
                    buf.document.text_before_cursor
                )
                if token is not None:
                    buf.start_completion(select_first=False)

            @self.bindings.add("backspace")
            def _backspace_refreshes_slash_menu(event):
                _refresh_slash_menu_after_deletion(
                    event, lambda b: b.delete_before_cursor(count=1)
                )

            @self.bindings.add("delete")
            def _delete_refreshes_slash_menu(event):
                _refresh_slash_menu_after_deletion(
                    event, lambda b: b.delete(count=1)
                )

            @self.bindings.add("c-m")
            def _enter_submits_or_backslash_newline(event):
                buf = event.current_buffer
                if buf.complete_state:
                    buf.complete_state = None
                    return
                text = buf.text
                pos = buf.cursor_position
                if pos > 0 and text[pos - 1] == "\\":
                    buf.delete_before_cursor(count=1)
                    buf.insert_text("\n")
                    return
                buf.validate_and_handle()

            @self.bindings.add("escape", "c-m")
            def _meta_or_shift_enter_inserts_newline(event):
                event.current_buffer.insert_text("\n")

            @self.bindings.add("c-o")
            def _expand_last(event):
                try:
                    from prompt_toolkit.application import run_in_terminal
                    run_in_terminal(self._do_expand_last)
                except Exception:
                    self._do_expand_last()

            @self.bindings.add("s-tab")  # type: ignore[attr-defined]
            def _cycle_permission_mode(event):  # type: ignore[no-untyped-def]
                """Shift+Tab: cycle through permission modes.

                Mirrors the TypeScript Ink reference's Shift+Tab binding
                for cycling through default → acceptEdits → plan →
                bypassPermissions → default.
                """
                from src.permissions import cycle_permission_mode

                ctx = self.tool_context
                if ctx is None:
                    return
                current_mode = ctx.permission_context.mode
                is_bypass_available = (
                    self._is_bypass_permissions_mode_available
                )
                # Build a context for cycle_permission_mode
                from src.permissions.types import ToolPermissionContext
                cycle_ctx = ToolPermissionContext(
                    mode=current_mode,
                    is_bypass_permissions_mode_available=is_bypass_available,
                )
                next_mode, next_ctx = cycle_permission_mode(cycle_ctx)
                # Update the REPL's permission state
                self._permission_mode = next_mode
                ctx.permission_context = next_ctx
                # Update the tool context's permission handler if mode changed
                if next_mode == "bypassPermissions":
                    ctx.permission_handler = lambda _tn, _msg, _sug: (True, False)
                    ctx.allow_docs = True
                else:
                    ctx.permission_handler = self._handle_permission_request
                    ctx.allow_docs = False

        # ---- PromptSession ----
        from prompt_toolkit import PromptSession

        self.prompt_session = PromptSession(
            history=FileHistory(str(history_file)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=self.completer,
            style=Style.from_dict({
                "prompt": "bold fg:ansiblue bg:#262626",
                "bottom-toolbar": "fg:#888888 bg:default",
                "completion-menu": "bg:default",
                "completion-menu.completion": "fg:#bfbfbf bg:default",
                "completion-menu.completion.current": "fg:#ffffff bg:#005f87 bold",
                "completion-menu.meta.completion": "fg:#7a7a7a bg:default",
                "completion-menu.meta.completion.current": "fg:#dadada bg:#005f87",
                "completion.command": "bold fg:ansigreen",
                "completion.tag": "italic fg:ansicyan",
                "completion.description": "fg:#9a9a9a",
            }),
            key_bindings=self.bindings,
            complete_while_typing=True,
            multiline=True,
            prompt_continuation=self._prompt_continuation,
            bottom_toolbar=self._bottom_toolbar,
        )

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

        # Register downstream runtime commands (/provider, /model)
        from clawcodex_ext.cli.runtime_commands import register_runtime_commands
        register_runtime_commands(self.command_registry)  # instance registry (autocomplete)
        register_runtime_commands(None)  # global registry (execute_command_sync lookup)

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
