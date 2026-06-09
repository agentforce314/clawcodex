"""Textual ``App`` subclass that hosts the Claw Codex TUI.

The app owns everything that must outlive a single screen push: the
``Session`` / ``Conversation``, the provider instance, the tool registry,
the tool context, the :class:`AppState`, and the
:class:`AgentBridge` that shuttles events between the agent-loop worker
thread and the UI.

Phase 1 boots the :class:`REPLScreen` on mount and delegates user
submissions to :class:`AgentBridge.submit`. Permission requests from
tools land here as :class:`PermissionRequested` messages, which the
screen then materialises as a modal.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from textual.app import App

_log_lock = threading.Lock()

def _log(msg: str) -> None:
    with _log_lock:
        with open('/tmp/tui_flow.log', 'a') as f:
            f.write(msg + '\n')

from src import __version__ as CLAW_VERSION
from src.agent import Session
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.registry import ToolRegistry

from .a11y import Announcer, describe_status
from .agent_bridge import AgentBridge
from .commands import (
    CommandDispatchResult,
    CommandSuggestion,
    build_command_suggestions,
    build_command_words,
    dispatch_local_command,
    dispatch_registry_command,
)
from .history_store import HistoryStore  # noqa: F401 (re-exported for tests)
from .messages import AssistantMessage, CancelRequested, ToolEventMessage
from .screens.cost_threshold import CostThresholdScreen
from .screens.diff_dialog import DiffDialogScreen, FileDiff
from .screens.effort_picker import EffortPickerScreen
from .screens.exit_flow import ExitFlowScreen
from .screens.history_search import HistoryEntry, HistorySearchScreen
from .screens.idle_return import IdleReturnScreen
from .screens.mcp_dialogs import McpListScreen, McpServer
from .screens.message_selector import MessageSelectorScreen, TranscriptMessage
from .screens.model_picker import ModelPickerScreen
from .screens.permission_mode_picker import PermissionModePickerScreen
from .screens.repl import REPLScreen
from .screens.resume_conversation import ResumeConversation
from .screens.theme_picker import ThemePickerScreen
from .state import AppState
from .terminal_chrome import (
    clear_terminal_title,
    disable_focus_events,
    enable_focus_events,
    ring_bell,
    set_tab_status,
    set_terminal_title,
)
from .theme import (
    get_palette,
    list_theme_names,
    resolve_auto_theme,
    textual_css_overrides,
)
from .widgets.transcript_view import Transcript


def _flatten_message_text(content: Any) -> str:
    """Normalise ``Message.content`` (string or block list) to text."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                kind = item.get("type")
                if kind in (None, "text"):
                    parts.append(str(item.get("text") or ""))
                elif kind == "tool_use":
                    parts.append(f"[tool:{item.get('name') or ''}]")
                else:
                    parts.append("")
        return "\n".join(p for p in parts if p).strip()
    return str(content)


class ClawCodexTUI(App):
    """Top-level Textual application for Claw Codex."""

    TITLE = "ClawCodex"
    SUB_TITLE = "interactive terminal"

    BINDINGS = [
        ("ctrl+c", "cancel_or_quit", "Cancel / Quit"),
        ("ctrl+d", "quit", "Quit"),
        ("ctrl+b", "agent_background", "Background agent"),
        ("ctrl+t", "toggle_thinking", "Toggle thinking"),
        ("shift+tab", "cycle_permission_mode", "Cycle permission mode"),
    ]

    def __init__(
        self,
        *,
        provider,
        provider_name: str,
        workspace_root: Path,
        tool_registry: ToolRegistry | None = None,
        tool_context: ToolContext | None = None,
        session: Session | None = None,
        max_turns: int = 20,
        stream: bool = True,
        theme_name: str | None = None,
        tail_follower: Any | None = None,
        resume_browse: bool = False,
        runtime_context: Any | None = None,
        append_system_prompt: str = "",
    ) -> None:
        super().__init__()
        self.runtime_context = runtime_context
        self._append_system_prompt = append_system_prompt
        self.provider = provider
        self.provider_name = provider_name
        self.workspace_root = Path(workspace_root)
        self.max_turns = max_turns
        self.stream = stream
        self.model = getattr(provider, "model", "unknown")
        self.session = session or Session.create(provider_name, self.model or "")
        self.tool_registry = tool_registry or build_default_registry(provider=provider)
        self.tool_context = tool_context or self._build_default_tool_context()
        # Theme is resolved once on boot; ``/theme`` can switch it
        # live via :meth:`apply_theme`.
        self.palette = get_palette(theme_name or self._resolve_theme_name())
        self.app_state = AppState(
            model=self.model or "",
            provider=provider_name,
        )
        self._repl_screen: REPLScreen | None = None
        self._command_context: Any | None = None
        # Transcript renderables captured at exit time so entry points
        # can dump them back to the main terminal scrollback after the
        # alt-screen tears down. Mirrors the TS ink behaviour where the
        # conversation the user saw stays on-screen after ``/exit``.
        self.exit_snapshot: list[Any] = []
        # Persistent prompt history used by the PromptInput (↑/↓) and
        # the /history slash-command dialog. The store is append-only
        # per turn and auto-rotates past ``max_entries``.
        self.history_store = HistoryStore()
        self._theme_name = (theme_name or self._resolve_theme_name())
        # Screen-reader announcer. The :class:`LiveRegion` widget is
        # bound in :meth:`on_mount` once the REPL screen is composed.
        self.announcer = Announcer(self)
        self._agent_bridge = AgentBridge(
            post_message=self._post_to_screen,
            session=self.session,
            provider=self.provider,
            tool_registry=self.tool_registry,
            tool_context=self.tool_context,
            app_state=self.app_state,
            run_worker=self.run_worker,
            max_turns=self.max_turns,
            stream=self.stream,
            tail_follower=tail_follower,
            append_system_prompt=self._append_system_prompt,
        )
        self._resume_browse = resume_browse

    # The base CSS for the REPL; Phase 1 uses Textual's default theme
    # variables ($primary, $surface, …) — palette overrides sit in
    # ``textual_css_overrides`` and are appended at class build time.
    CSS = ""

    def _resolve_theme_name(self) -> str:
        try:
            from src.config import load_config

            cfg = load_config() or {}
            return cfg.get("theme") or "dark"
        except Exception:
            return "dark"

    # ---- lifecycle ----
    def on_mount(self) -> None:
        import sys
        print(f"[DEBUG app] on_mount called, session has {len(self.session.conversation.messages)} messages", file=sys.stderr)
        # Apply palette-derived CSS on top of the component defaults so
        # the chrome picks up the correct background / foreground even
        # when Textual's internal theme doesn't cover every slot.
        try:
            self.stylesheet.add_source(
                textual_css_overrides(self.palette),
                path="palette-overrides",
            )
            self.stylesheet.parse()
        except Exception:
            pass

        self._repl_screen = REPLScreen(
            version=CLAW_VERSION,
            provider=self.provider_name,
            model=self.model,
            workspace_root=self.workspace_root,
            words_provider=self._slash_command_words,
            suggestions_provider=self._slash_command_suggestions,
            # Pass the live BaseProvider so the status line's advisor
            # segment can call ``decide_advisor_mode(provider, ...)``
            # and show the correct mode label (server/client/inactive).
            provider_instance=self.provider,
        )
        self.push_screen(self._repl_screen)

        # Replay conversation history from a resumed session so the
        # transcript widget shows the prior context immediately.
        if self.session.conversation.messages:
            self._replay_history()

        # Terminal chrome: set a descriptive title, enable DEC 1004
        # focus reporting, and mark the tab idle. The app-state
        # observer below keeps title + tab status in sync with agent
        # activity.
        self._last_thinking: bool = self.app_state.is_thinking
        self._sync_terminal_title()
        set_tab_status("idle")
        try:
            enable_focus_events()
        except Exception:
            pass
        self._state_unsub = self.app_state.subscribe(self._on_state_change)

        # If --resume was given without a SESSION_ID, show the session
        # browser so the user can pick a session to resume.
        if self._resume_browse:
            self._show_resume_browser()

    def on_unmount(self) -> None:
        # Best-effort cleanup so we don't leave stale chrome on the host.
        try:
            self._state_unsub()  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            set_tab_status(None)
            clear_terminal_title()
            disable_focus_events()
        except Exception:
            pass
        # Fallback capture in case ``exit()`` wasn't the path out (e.g.
        # Ctrl+C / SIGTERM). Entry points will print whatever landed
        # here to the host shell after the alt-screen exits.
        if not self.exit_snapshot:
            self._capture_exit_snapshot()
        # Save session as a fallback (if exit() already saved, save is
        # a no-op because the data is already on disk).
        try:
            self.session.save()
        except Exception:
            pass

    # ---- exit / snapshot ----------------------------------------------
    def _capture_exit_snapshot(self) -> None:
        """Collect the transcript's renderables into :attr:`exit_snapshot`.

        Called from :meth:`exit` and (as a fallback) :meth:`on_unmount`
        so no matter which shutdown path fires we preserve what the
        user saw. Failures are swallowed — a blank snapshot is fine,
        but raising would mask a normal exit.
        """

        if self.exit_snapshot or self._repl_screen is None:
            return
        try:
            self.exit_snapshot = list(self._repl_screen.transcript.snapshot())
        except Exception:
            self.exit_snapshot = []

    def exit(self, result=None, return_code=0, message=None):  # type: ignore[override]
        """Capture transcript and save session before handing control back to Textual.

        Overriding ``exit()`` lets the entry-point reprint the
        conversation to the host terminal once the alt-screen unwinds,
        matching the TS ink reference's non-fullscreen UX where
        `/exit` leaves the printed text intact in scrollback.

        Saving the session here ensures it is persisted regardless of
        which exit path fires (``/exit``, Ctrl+D, exit-flow dialog).
        """

        self._capture_exit_snapshot()
        # Save session state so it can be resumed later.
        try:
            self.session.save()
        except Exception:
            pass
        return super().exit(result, return_code=return_code, message=message)

    def _on_state_change(self) -> None:
        """React to :class:`AppState` changes to refresh terminal chrome.

        Title reflects the active verb; tab status flips between
        ``busy`` (agent thinking) and ``idle`` (prompt ready); a
        terminal bell rings on the idle→thinking→idle edge to
        announce turn completion, matching the TS reference's
        idle-notification.
        """

        thinking = self.app_state.is_thinking
        if thinking != self._last_thinking:
            set_tab_status("busy" if thinking else "idle")
            if not thinking:
                # Turn completed — poke the host so the user notices
                # even when they tabbed away.
                try:
                    ring_bell()
                except Exception:
                    pass
                self.announcer.announce(
                    describe_status("idle"), level="polite", notify=False
                )
            else:
                self.announcer.announce(
                    describe_status("busy", verb=self.app_state.verb),
                    level="polite",
                    notify=False,
                )
            self._last_thinking = thinking
        self._sync_terminal_title()

    def _sync_terminal_title(self) -> None:
        try:
            state = self.app_state
            verb = state.verb if state.is_thinking else "Ready"
            title = f"ClawCodex — {state.model or self.provider_name}: {verb}"
            set_terminal_title(title)
        except Exception:
            pass

    # ---- bindings ----
    def action_cancel_or_quit(self) -> None:
        # Try to cancel the in-flight agent run first. Only exit if
        # there is no active run or the cancel fails.
        if self._agent_bridge.cancel():
            self.announcer.announce("Cancelling…", level="assertive", notify=False)
            return
        self.exit()

    def action_agent_background(self) -> None:
        """Handle Ctrl+B — signal agent to continue in background, save
        session, and exit to terminal shell.

        Implements the Fork-Continue pattern:
        * If the agent is currently busy, we cancel the foreground run,
          wait briefly for it to settle, then launch the background
          runner so the agent keeps working after the TUI exits.
        * If the agent is idle, we fall back to the simpler
          ``__FULL_EXIT__`` path.

        The exit marker tells the calling entry point whether a
        background agent was actually spawned, so it can print the
        appropriate hint after the alt-screen tears down.
        """
        # Signal agent to continue running in background.
        try:
            from src.agent.background_state import signal_background
            signal_background()
        except Exception:
            pass
        # Persist session so --resume can find it later.
        try:
            self.session.save()
        except Exception:
            pass
        sid = getattr(self.session, "session_id", None) or ""

        if self.app_state.is_thinking:
            # Agent is busy — cancel the foreground run, wait briefly,
            # then fork into the background runner.
            self._agent_bridge.cancel()
            import time
            time.sleep(0.15)
            has_bg_agent = False
            try:
                from src.agent.background_runner import launch_background_runner
                launch_background_runner(
                    session=self.session,
                    provider=self.provider,
                    tool_registry=self.tool_registry,
                    tool_context=self.tool_context,
                    max_turns=self.max_turns,
                )
                has_bg_agent = True
            except Exception:
                pass
            self.exit(result=("__BACKGROUND_EXIT__", sid, has_bg_agent))
        else:
            # Agent is idle — simple exit with session ID for resume hint.
            self.exit(result=("__FULL_EXIT__", sid))

    def action_toggle_thinking(self) -> None:
        """Ctrl+T: toggle thinking content visibility in all thinking rows."""
        if self._repl_screen is None:
            return
        transcript = self._repl_screen.transcript
        # Toggle all thinking rows
        from src.tui.widgets.messages.assistant_thinking import (
            AssistantThinkingMessage,
            ThinkingToggled,
        )

        expanded = True
        for row in transcript.query(AssistantThinkingMessage):
            row.toggle()
            expanded = row.expanded

        label = "expanded" if expanded else "collapsed"
        transcript.append_system(f"Thinking content: {label}", style="muted")
        self.announcer.announce(f"Thinking {label}")

    def action_cycle_permission_mode(self) -> None:
        """Shift+Tab: cycle through permission modes.

        Cycles: default → acceptEdits → plan → bypassPermissions (if available) → default.
        """
        from src.permissions import cycle_permission_mode

        ctx = self.tool_context
        if ctx is None or ctx.permission_context is None:
            return
        current_mode = ctx.permission_context.mode
        is_bypass_available = False
        try:
            from src.permissions.modes import has_allow_bypass_permissions_mode
            is_bypass_available = has_allow_bypass_permissions_mode()
        except Exception:
            pass
        cycle_ctx = ctx.permission_context.__class__(
            mode=current_mode,
            is_bypass_permissions_mode_available=is_bypass_available,
        )
        next_mode, next_ctx = cycle_permission_mode(cycle_ctx)
        ctx.permission_context = next_ctx
        if next_mode == "bypassPermissions":
            ctx.permission_handler = lambda _tn, _msg, _sug: (True, False)
            ctx.allow_docs = True
        else:
            ctx.permission_handler = self._agent_bridge._permission_handler
            ctx.allow_docs = False
        if self._repl_screen is not None:
            self._repl_screen.transcript.append_system(
                f"Permission mode: {next_mode}", style="muted"
            )
            self._repl_screen.status_bar.set_permission_mode(next_mode)
        self.announcer.announce(f"Permission mode: {next_mode}")

    # ---- local command dispatcher ----
    def handle_local_slash_command(self, text: str, transcript: Transcript) -> bool:
        """Return ``True`` if the command was handled without hitting the agent.

        The dispatcher tries the local built-ins first (``/exit``,
        ``/help``, …), then falls through to the shared
        :mod:`src.command_system` registry. Commands that produce a
        prompt (``/init``) forward the prompt back to the agent bridge.
        """

        result = dispatch_local_command(
            text,
            session=self.session,
            workspace_root=self.workspace_root,
            tool_registry=self.tool_registry,
        )
        if result.handled:
            self._apply_command_result(result, transcript)
            return True

        # Fall through to the async command registry. We run it via the
        # asyncio loop that Textual already runs on.
        async def _run() -> CommandDispatchResult:
            return await dispatch_registry_command(
                text,
                command_context=self._ensure_command_context(),
            )

        # Schedule the async work on the Textual loop; if it comes back
        # handled we emit the appropriate UI response.
        self.run_worker(self._dispatch_registry_async(text, transcript), exclusive=False, name="slash-cmd")
        return True

    async def _dispatch_registry_async(self, text: str, transcript: Transcript) -> None:
        result = await dispatch_registry_command(
            text,
            command_context=self._ensure_command_context(),
        )
        if not result.handled:
            # Unknown command — show the raw text as a user prompt so
            # the agent can react to it, matching legacy REPL behavior.
            transcript.append_user(text)
            self.submit_to_agent(text)
            return
        self._apply_command_result(result, transcript)

    def _apply_command_result(
        self,
        result: CommandDispatchResult,
        transcript: Transcript,
    ) -> None:
        if result.error:
            transcript.append_system(result.error, style="error")
            return
        if result.open_dialog:
            self._open_phase2_dialog(result.open_dialog, transcript)
            return
        if result.system_text == "__exit__":
            self._confirm_exit(transcript)
            return
        if result.system_text == "__background__":
            # Ctrl+B: signal agent to continue in background,
            # persist session for --resume, exit with session ID so the
            # calling code prints the resume hint after teardown.
            try:
                from src.agent.background_state import signal_background
                signal_background()
            except Exception:
                pass
            try:
                self.session.save()
            except Exception:
                pass
            sid = getattr(self.session, "session_id", None) or ""
            self.exit(result=("__FULL_EXIT__", sid))
            return
        if result.system_text == "__repl__":
            # /repl: cleanly exit TUI page, return to CLI REPL.
            # No dialog, no background signal — just go back.
            self.exit()
            return
        if result.system_text == "__clear__":
            transcript.clear_transcript()
            self._agent_bridge.reset_advisor_dedup()
            return
        if result.system_text in ("__stream_on__", "__stream_off__", "__stream_toggle__", "__stream_status__"):
            if result.system_text == "__stream_on__":
                self.stream = True
            elif result.system_text == "__stream_off__":
                self.stream = False
            elif result.system_text == "__stream_toggle__":
                self.stream = not self.stream
            self._agent_bridge._stream = self.stream
            status = "enabled" if self.stream else "disabled"
            transcript.append_system(f"Stream mode {status}.")
            return
        if result.system_text:
            transcript.append_system(result.system_text, style="muted")
        if result.prompt_text:
            transcript.append_user(f"(from slash command) {result.prompt_text[:80]}…")
            self.submit_to_agent(result.prompt_text)

    # ---- Phase 2 dialog dispatcher -------------------------------------
    def _open_phase2_dialog(self, name: str, transcript: Transcript) -> None:
        """Push the modal screen for ``name`` from the slash command.

        ``name`` is one of the values produced by
        :func:`dispatch_local_command`; unknown names degrade to a
        muted system message.
        """

        if name == "model":
            self._open_model_picker(transcript)
        elif name == "effort":
            self._open_effort_picker(transcript)
        elif name == "history":
            self._open_history_search(transcript)
        elif name == "cost":
            self._open_cost_threshold(transcript)
        elif name == "idle":
            self._open_idle_return(transcript)
        elif name == "theme":
            self._open_theme_picker(transcript)
        elif name == "diff":
            self._open_diff_dialog(transcript)
        elif name == "mcp":
            self._open_mcp_list(transcript)
        elif name in ("rewind", "messages"):
            self._open_message_selector(transcript)
        elif name == "tasks":
            self._open_tasks_dialog(transcript)
        elif name == "resume":
            self._show_resume_browser()
        elif name == "permission":
            self._open_permission_mode_picker(transcript)
        else:
            transcript.append_system(f"Dialog '{name}' not available.", style="muted")

    def _open_model_picker(self, transcript: Transcript) -> None:
        models = self._list_available_models()

        def _on_selected(model_id: str | None) -> None:
            if not model_id or model_id == self.model:
                self._restore_prompt_focus()
                return
            self.model = model_id
            try:
                if hasattr(self.provider, "model"):
                    setattr(self.provider, "model", model_id)
            except Exception:
                pass
            self.app_state.model = model_id
            transcript.append_system(f"Model switched to {model_id}.", style="muted")
            if self._repl_screen is not None:
                self._repl_screen.status_bar.refresh_identity(model=model_id)
            self.announcer.announce(f"Model switched to {model_id}.")
            self._restore_prompt_focus()

        self.announcer.announce("Opened model picker.", notify=False)
        self.push_screen(
            ModelPickerScreen(
                models=models,
                current_model=self.model,
            ),
            callback=_on_selected,
        )

    def _open_effort_picker(self, transcript: Transcript) -> None:
        current = getattr(self.app_state, "effort", None) or None

        def _on_selected(result: tuple[str | None, bool]) -> None:
            effort, persisted = result
            self._restore_prompt_focus()
            if not persisted:
                return
            setattr(self.app_state, "effort", effort)
            transcript.append_system(
                f"Reasoning effort set to {effort or 'auto'}.", style="muted"
            )
            self.announcer.announce(
                f"Reasoning effort set to {effort or 'auto'}."
            )

        self.announcer.announce("Opened effort picker.", notify=False)
        self.push_screen(EffortPickerScreen(current=current), callback=_on_selected)

    def _open_history_search(self, transcript: Transcript) -> None:
        records = self.history_store.recent(limit=500)
        entries = [
            HistoryEntry(prompt=r.prompt, timestamp=r.timestamp) for r in records
        ]
        if not entries:
            transcript.append_system(
                "History is empty — run some prompts first.", style="muted"
            )
            return

        def _on_selected(result: str | None) -> None:
            self._restore_prompt_focus()
            if not result:
                return
            if self._repl_screen is not None:
                self._repl_screen.prompt_input.set_value(result)
                self._repl_screen.prompt_input.focus_input()
            self.announcer.announce("Prompt restored from history.", notify=False)

        self.announcer.announce("Opened history search.", notify=False)
        self.push_screen(HistorySearchScreen(entries=entries), callback=_on_selected)

    def _open_cost_threshold(self, transcript: Transcript) -> None:
        tokens = self.app_state.usage.get("input_tokens", 0) + self.app_state.usage.get(
            "output_tokens", 0
        )
        # Rough estimate: $5 per 1M tokens. Phase 2 keeps this simple;
        # real per-model rates land with /cost refactor in Phase 3.
        estimate = (tokens / 1_000_000) * 5.0
        self.announcer.announce(
            f"Session cost estimate ${estimate:.2f}.", notify=False
        )
        self.push_screen(
            CostThresholdScreen(provider=self.provider_name, amount_usd=estimate),
            callback=lambda _=None: self._restore_prompt_focus(),
        )

    def _open_idle_return(self, transcript: Transcript) -> None:
        tokens = self.app_state.usage.get("input_tokens", 0)

        def _on_choice(action: str) -> None:
            if action == "clear":
                transcript.clear_transcript()
                self._agent_bridge.reset_advisor_dedup()
                transcript.append_system("Conversation cleared.", style="muted")
                self.announcer.announce("Conversation cleared.")
            elif action == "never":
                transcript.append_system(
                    "Idle-return prompts disabled for this session.", style="muted"
                )

        self.announcer.announce("Idle return prompt open.", notify=False)
        self.push_screen(
            IdleReturnScreen(
                idle_minutes=0,
                total_input_tokens=tokens,
                on_choice=_on_choice,
            ),
            callback=lambda _=None: self._restore_prompt_focus(),
        )

    def _open_theme_picker(self, transcript: Transcript) -> None:
        original_theme = self._theme_name

        def _on_preview(name: str | None) -> None:
            # Live-preview the highlighted theme; Esc restores the
            # original one so we don't leak an unintended swap.
            target = name or original_theme
            if target and target != self._theme_name:
                self.apply_theme(target, transcript=None)

        def _on_selected(name: str | None) -> None:
            self._restore_prompt_focus()
            if not name:
                # User cancelled — restore the starting theme.
                if self._theme_name != original_theme:
                    self.apply_theme(original_theme, transcript=None)
                return
            self.apply_theme(name, transcript=transcript)

        self.announcer.announce("Opened theme picker.", notify=False)
        self.push_screen(
            ThemePickerScreen(
                themes=list_theme_names(),
                current=self._theme_name,
                on_preview=_on_preview,
            ),
            callback=_on_selected,
        )

    def _open_permission_mode_picker(self, transcript: Transcript) -> None:
        current_mode = "default"
        try:
            from src.permissions.modes import to_external_permission_mode
            ctx = self.tool_context
            if ctx is not None and ctx.permission_context is not None:
                current_mode = to_external_permission_mode(
                    ctx.permission_context.mode or "default"
                )
        except Exception:
            pass

        is_bypass_available = False
        try:
            from src.permissions.modes import has_allow_bypass_permissions_mode
            is_bypass_available = has_allow_bypass_permissions_mode()
        except Exception:
            pass

        def _on_selected(mode: str | None) -> None:
            self._restore_prompt_focus()
            if not mode:
                return
            try:
                from src.permissions.updates import apply_permission_update
                from src.permissions.types import PermissionUpdateSetMode

                ctx = self.tool_context
                if ctx is not None and ctx.permission_context is not None:
                    new_ctx = apply_permission_update(
                        ctx.permission_context,
                        PermissionUpdateSetMode(
                            type="setMode",
                            destination="session",
                            mode=mode,
                        ),
                    )
                    ctx.permission_context = new_ctx
                    # Update the permission handler if mode changed
                    if mode == "bypassPermissions":
                        ctx.permission_handler = lambda _tn, _msg, _sug: (True, False)
                        ctx.allow_docs = True
                    else:
                        # Re-wire the UI permission handler from the bridge
                        ctx.permission_handler = self._agent_bridge._permission_handler
                        ctx.allow_docs = False
                    transcript.append_system(
                        f"Permission mode set to {mode}.", style="muted"
                    )
                    if self._repl_screen is not None:
                        self._repl_screen.status_bar.set_permission_mode(mode)
                    self.announcer.announce(f"Permission mode: {mode}.")
            except Exception as exc:
                transcript.append_system(
                    f"Failed to set permission mode: {exc}", style="error"
                )

        self.announcer.announce("Opened permission mode picker.", notify=False)
        self.push_screen(
            PermissionModePickerScreen(
                current_mode=current_mode,
                is_bypass_available=is_bypass_available,
                on_select=_on_selected,
            ),
        )

    # ---- Phase 3 dialogs ----
    def _open_diff_dialog(self, transcript: Transcript) -> None:
        """Show pending file diffs, if the provider can surface them.

        We collect diffs from ``app_state.pending_diffs`` (populated by
        the file-edit tools) or the conversation's most-recent tool
        results. If nothing is available we drop a muted note instead
        of opening an empty dialog.
        """

        files: list[FileDiff] = []
        pending = getattr(self.app_state, "pending_diffs", None) or []
        for entry in pending:
            if isinstance(entry, FileDiff):
                files.append(entry)
            elif isinstance(entry, dict) and "patch" in entry and "path" in entry:
                files.append(FileDiff(path=str(entry["path"]), patch=str(entry["patch"])))

        if not files:
            transcript.append_system(
                "No pending diffs to display.", style="muted"
            )
            return

        self.announcer.announce(
            f"Diff dialog open. {len(files)} file(s) changed.", notify=False
        )
        self.push_screen(
            DiffDialogScreen(files=files),
            callback=lambda _=None: self._restore_prompt_focus(),
        )

    def _open_message_selector(self, transcript: Transcript) -> None:
        messages = self._collect_transcript_messages()
        if not messages:
            transcript.append_system(
                "Nothing to rewind — the transcript is empty.", style="muted"
            )
            return

        def _on_choice(result: tuple[int, str]) -> None:
            index, action = result
            self._restore_prompt_focus()
            if action == "cancel" or index < 0:
                return
            selected = next((m for m in messages if m.index == index), None)
            if selected is None:
                return
            if action == "restore" and self._repl_screen is not None:
                self._repl_screen.prompt_input.set_value(selected.text)
                self._repl_screen.prompt_input.focus_input()
                transcript.append_system(
                    f"Restored prompt from message #{index}.", style="muted"
                )
                self.announcer.announce(
                    f"Restored prompt from message {index}.", notify=False
                )
            elif action == "summarize":
                transcript.append_system(
                    f"Summarise-from-here requested for message #{index}.",
                    style="muted",
                )
                self.announcer.announce(
                    f"Summarise requested for message {index}."
                )

        self.announcer.announce(
            f"Message selector open. {len(messages)} message(s).", notify=False
        )
        self.push_screen(
            MessageSelectorScreen(messages=messages, on_choice=None),
            callback=_on_choice,
        )

    def _collect_transcript_messages(self) -> list[TranscriptMessage]:
        out: list[TranscriptMessage] = []
        try:
            conversation = self.session.conversation
            history = getattr(conversation, "messages", None) or []
        except Exception:
            return out

        idx = 0
        for msg in history:
            role = getattr(msg, "role", None)
            if role is None and isinstance(msg, dict):
                role = msg.get("role")
            content = getattr(msg, "content", None)
            if content is None and isinstance(msg, dict):
                content = msg.get("content")
            if not role or role not in ("user", "assistant"):
                continue
            text = _flatten_message_text(content)
            if not text.strip():
                continue
            out.append(TranscriptMessage(index=idx, kind=role, text=text))
            idx += 1
        return out

    def _open_mcp_list(self, transcript: Transcript) -> None:
        servers = self._collect_mcp_servers()
        if not servers:
            transcript.append_system(
                "No MCP servers configured.", style="muted"
            )
            return
        self.announcer.announce(
            f"MCP servers list open. {len(servers)} server(s).", notify=False
        )
        self.push_screen(
            McpListScreen(servers=servers),
            callback=lambda _=None: self._restore_prompt_focus(),
        )

    def _collect_mcp_servers(self) -> list[McpServer]:
        try:
            from src.config import load_config

            cfg = load_config() or {}
            raw = cfg.get("mcp_servers") or cfg.get("mcpServers") or {}
        except Exception:
            raw = {}
        servers: list[McpServer] = []
        if isinstance(raw, dict):
            for server_id, entry in raw.items():
                name = entry.get("name", server_id) if isinstance(entry, dict) else server_id
                status = entry.get("status", "disconnected") if isinstance(entry, dict) else "disconnected"
                tools = entry.get("tools", []) if isinstance(entry, dict) else []
                servers.append(
                    McpServer(
                        id=str(server_id),
                        name=str(name),
                        status=status,  # type: ignore[arg-type]
                        tools=list(tools) if isinstance(tools, list) else [],
                    )
                )
        return servers

    def _open_tasks_dialog(self, transcript: Transcript) -> None:
        # The background task panel lives on the REPL screen; the slash
        # command just routes focus to it rather than stacking a modal.
        if self._repl_screen is not None and hasattr(
            self._repl_screen, "focus_task_panel"
        ):
            try:
                self._repl_screen.focus_task_panel()
                return
            except Exception:
                pass
        transcript.append_system(
            "Task panel focus is not available in this build.", style="muted"
        )

    def _confirm_exit(self, transcript: Transcript) -> None:
        """Push :class:`ExitFlowScreen` instead of quitting immediately."""

        def _on_choice(action: str) -> None:
            if action == "quit":
                self.exit()
            elif action == "quit-clear":
                transcript.clear_transcript()
                self.exit()
            else:  # "cancel"
                self._restore_prompt_focus()

        self.announcer.announce(
            "Exit confirmation open.", level="assertive", notify=False
        )
        self.push_screen(
            ExitFlowScreen(
                has_inflight_work=self.app_state.is_thinking,
                on_choice=_on_choice,
            )
        )

    # ---- focus helpers ----
    def _restore_prompt_focus(self) -> None:
        """Return keyboard focus to the prompt input after a modal closes.

        Modals that dismiss via the ``callback=`` path don't
        automatically restore focus on the previous screen, so we do
        it explicitly for every dialog close. No-op when the REPL
        screen hasn't mounted yet.
        """

        if self._repl_screen is None:
            return
        try:
            self._repl_screen.prompt_input.focus_input()
        except Exception:
            pass

    # ---- theme live-switch ----
    def apply_theme(self, name: str, *, transcript: Transcript | None = None) -> None:
        """Hot-swap the palette and refresh the stylesheet overrides."""

        self.palette = get_palette(name)
        self._theme_name = name
        try:
            self.stylesheet.add_source(
                textual_css_overrides(self.palette),
                path="palette-overrides",
            )
            self.stylesheet.parse()
            self.refresh(layout=True)
        except Exception:
            pass
        if transcript is not None:
            transcript.append_system(f"Theme set to {name}.", style="muted")
            self.announcer.announce(f"Theme set to {name}.", notify=False)

    # ---- model discovery ----
    def _list_available_models(self) -> list[str]:
        """Return a best-effort list of models for the active provider."""

        try:
            if hasattr(self.provider, "list_models"):
                models = list(self.provider.list_models() or [])  # type: ignore[attr-defined]
                if models:
                    return [str(m) for m in models]
        except Exception:
            pass
        try:
            from src.config import get_provider_config

            cfg = get_provider_config(self.provider_name) or {}
            models = cfg.get("models")
            if isinstance(models, list) and models:
                return [str(m) for m in models]
            default = cfg.get("default_model")
            if default:
                return [str(default)]
        except Exception:
            pass
        # Fallback: just the active model.
        return [self.model or "default"]

    def _ensure_command_context(self) -> Any:
        if self._command_context is not None:
            return self._command_context
        try:
            from src.command_system.builtins import register_builtin_commands
            from src.command_system.engine import create_command_context
            from src.cost_tracker import CostTracker
            from src.history import HistoryLog

            register_builtin_commands(None)
            self._command_context = create_command_context(
                workspace_root=self.workspace_root,
                conversation=self.session.conversation,
                cost_tracker=CostTracker(),
                history=HistoryLog(),
                provider=self.provider,
                tool_registry=self.tool_registry,
                tool_context=self.tool_context,
                runtime_context=self.runtime_context,
            )
        except Exception:
            self._command_context = None
        return self._command_context

    # ---- agent loop plumbing ----
    def submit_to_agent(self, prompt: str) -> None:
        ## _log(f'[app.py] submit_to_agent called: {prompt}')
        # Track last user input in session metadata for the session browser.
        self._update_metadata_last_input(prompt)
        try:
            self.history_store.append(prompt)
        except Exception:
            pass
        ## _log(f'[app.py] calling _agent_bridge.submit')
        submitted = self._agent_bridge.submit(prompt)
        ## _log(f'[app.py] _agent_bridge.submit returned: {submitted}')
        if not submitted:
            # If the bridge is busy we queue the prompt for the next
            # turn so the user can keep typing. Phase 2 adds a visible
            # queued-prompts pill in the status line.
            self.app_state.queued_prompts.append(prompt)

    def on_cancel_requested(self, _: CancelRequested) -> None:
        """ESC from the prompt — cancel the in-flight agent run, if any."""

        if self._agent_bridge.cancel():
            self.announcer.announce("Cancelling…", level="assertive", notify=False)

    # ---- helpers ----
    def _update_metadata_last_input(self, text: str) -> None:
        """Update the ``last_user_input`` field in SessionStorage metadata."""
        if not self.session:
            return
        try:
            from src.services.session_storage import SessionStorage
            storage = SessionStorage(session_id=self.session.session_id)
            storage.update_metadata(last_user_input=text[:200])  # cap at 200 chars
        except Exception:
            pass

    def _show_resume_browser(self) -> None:
        """Push the ResumeConversation modal so the user can pick a session."""
        screen = ResumeConversation()
        self.push_screen(screen, self._on_session_selected)

    def _on_session_selected(self, session_id: str | None) -> None:
        """Callback after the user picks a session from the resume browser."""
        if session_id is None:
            # User cancelled — start a fresh session.
            return
        try:
            from clawcodex_ext.agent.session_ext import resume_session_with_tail

            resumed, tail = resume_session_with_tail(session_id)
            # Swap session + bridge.
            self.session = resumed
            self._agent_bridge._session = resumed
            if tail is not None:
                self._agent_bridge._tail_follower = tail
                self._agent_bridge._start_tail_follower()
            # Replay the restored conversation into the transcript.
            if resumed.conversation.messages and self._repl_screen is not None:
                self._replay_history()
        except Exception:
            pass

    def _build_default_tool_context(self) -> ToolContext:
        # ``ask_user`` and ``permission_handler`` are wired by
        # :class:`AgentBridge.__init__` (see ``tui/agent_bridge.py``)
        # which mounts the bridge-to-UI plumbing that posts modal
        # requests and blocks the worker thread on user input. We
        # intentionally leave the defaults ``None`` here so that any
        # code path that constructs a tool context without the bridge
        # fails loudly instead of silently swallowing user questions
        # the way the old no-op lambda did.
        return ToolContext(workspace_root=self.workspace_root)

    def _replay_history(self) -> None:
        """Replay conversation messages from a resumed session to the transcript.

        Emits ``AssistantMessage`` / ``ToolEventMessage`` for each historical
        message so the transcript widget shows the prior context immediately
        after ``--resume``. Only called from :meth:`on_mount` when the
        session has existing messages.
        """
        for msg in self.session.conversation.messages:
            role = getattr(msg, "role", None) or ""
            content = getattr(msg, "content", None) or ""
            if role == "user":
                # Render user messages (S-R2 fix).  User messages are
                # posted through the transcript directly so the resume
                # view shows the full conversation context.
                text = _flatten_message_text(content)
                if text and self._repl_screen is not None:
                    self._repl_screen.transcript.append_user(text)
                continue
            if role == "assistant":
                text = _flatten_message_text(content)
                if text:
                    self._post_to_screen(AssistantMessage(text=text))
                # Replay tool_use / tool_result / thinking blocks from the content list.
                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        kind = item.get("type")
                        if kind == "tool_use":
                            self._post_to_screen(
                                ToolEventMessage(
                                    kind="tool_use",
                                    tool_name=item.get("name", ""),
                                    tool_input=item.get("input"),
                                    tool_use_id=item.get("id"),
                                )
                            )
                        elif kind == "tool_result":
                            self._post_to_screen(
                                ToolEventMessage(
                                    kind="tool_result",
                                    tool_name="",
                                    tool_output=item.get("content"),
                                    tool_use_id=item.get("tool_use_id"),
                                    is_error=bool(item.get("is_error")),
                                )
                            )
                        elif kind == "thinking":
                            # Replay thinking content from historical session.
                            thinking_text = item.get("thinking", "") or ""
                            if thinking_text and self._repl_screen is not None:
                                self._repl_screen.transcript.append_thinking_chunk(thinking_text)
                        elif kind == "redacted_thinking":
                            # Replay redacted thinking with redacted=True.
                            data = item.get("data", "") or ""
                            if data and self._repl_screen is not None:
                                self._repl_screen.transcript.append_thinking_chunk(data, redacted=True)

    def _slash_command_words(self) -> list[str]:
        return build_command_words(self.workspace_root, self.tool_context)

    def _slash_command_suggestions(self) -> list[CommandSuggestion]:
        return build_command_suggestions(self.workspace_root, self.tool_context)

    def _post_to_screen(self, message: Any) -> None:
        target = self._repl_screen or self
        try:
            target.post_message(message)
        except Exception:
            pass
