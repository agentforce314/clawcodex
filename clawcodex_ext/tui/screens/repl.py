"""Main REPL screen, rebuilt on top of :class:`FullscreenLayout`.

Composition (Phase 1):

* ``scroll``   — :class:`StartupHeader` + :class:`TranscriptView`.
* ``overlay``  — reserved for the in-region tool permission overlay
  (Phase 2 will mount ``PermissionRequest`` here without taking over
  the whole screen like the Phase 1 modal does).
* ``modal``    — reserved for centered slash-JSX panels (Phase 3).
* ``bottom``   — :class:`StatusLine` + :class:`PromptInput`.

Event wiring:

* :class:`PromptSubmitted` → slash dispatcher → agent bridge.
* :class:`AssistantChunk`  → active streaming row.
* :class:`AssistantMessage` → finalise the streaming row.
* :class:`ToolEventMessage` → transcript tool rows.
* :class:`PermissionRequested` → push :class:`PermissionModal`.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, TYPE_CHECKING

_log_lock = threading.Lock()

def _log(msg: str) -> None:
    with _log_lock:
        with open('/tmp/tui_flow.log', 'a') as f:
            f.write(msg + '\n')

from textual.app import ComposeResult
from textual.screen import Screen

from ..messages import (
    AdvisorEventMessage,
    AgentRunFinished,
    AgentRunStarted,
    AskUserQuestionRequested,
    AskUserQuestionResolved,
    AssistantChunk,
    AssistantMessage,
    PermissionModeCycleRequested,
    PermissionRequested,
    PermissionResolved,
    StateChanged,
    ThinkingChunk,
    ToolEventMessage,
)
from ..a11y import LiveRegion, aria_label
from ..widgets.fullscreen_layout import FullscreenLayout
from ..widgets.header import StartupHeader
from ..widgets.prompt_input import PromptInput, PromptSubmitted
from ..widgets.status_line import StatusLine
from ..widgets.transcript_view import Transcript

if TYPE_CHECKING:  # pragma: no cover
    from ..app import ClawCodexTUI
    from ..commands import CommandSuggestion


from textual.binding import Binding

class REPLScreen(Screen):
    """Composes the interactive TUI layout."""

    BINDINGS = [
        ("ctrl+l", "clear_transcript", "Clear transcript"),
        ("ctrl+t", "toggle_thinking", "Toggle thinking"),
        Binding("shift+tab", "cycle_permission_mode", "Cycle permission mode", priority=True),
    ]

    DEFAULT_CSS = """
    REPLScreen {
        layout: vertical;
    }
    """

    def __init__(
        self,
        *,
        version: str,
        provider: str,
        model: str,
        workspace_root: Path,
        words_provider: Callable[[], list[str]],
        suggestions_provider: Callable[[], list["CommandSuggestion"]] | None = None,
        provider_instance: object | None = None,
    ) -> None:
        super().__init__()
        self._version = version
        self._provider = provider
        self._model = model
        self._workspace_root = Path(workspace_root)
        self._words_provider = words_provider
        self._suggestions_provider = suggestions_provider
        self._provider_instance = provider_instance

        self.header_widget = StartupHeader(
            version=version,
            model=model,
            provider=provider,
            workspace_root=self._workspace_root,
        )
        self.transcript = Transcript()
        self.status_bar = StatusLine(
            provider=provider,
            model=model,
            workspace_root=self._workspace_root,
            provider_instance=provider_instance,
        )
        self.prompt_input = PromptInput(
            words_provider=words_provider,
            suggestions_provider=suggestions_provider,
        )
        # ARIA live region — stays height: 1 and only announces the
        # most recent status change. Mounted just above the status
        # bar so it's adjacent to the prompt for single-sweep reads.
        self.live_region = LiveRegion(aria_label="Status")
        # Note: intentionally NOT setting tooltip via aria_label on transcript
        # to avoid the hover-tooltip popup that follows the mouse cursor.
        aria_label(self.prompt_input, "Prompt input — type a message, or '/' for commands")
        # ``layout`` is a reserved attribute on Textual screens; use a
        # private attribute for our parity shell.
        self._fullscreen = FullscreenLayout()

    def compose(self) -> ComposeResult:
        yield self._fullscreen

    def on_mount(self) -> None:
        self._fullscreen.scroll_region().mount(self.header_widget)
        self._fullscreen.scroll_region().mount(self.transcript)
        self._fullscreen.bottom_region().mount(self.live_region)
        self._fullscreen.bottom_region().mount(self.status_bar)
        self._fullscreen.bottom_region().mount(self.prompt_input)
        # Bind the status line to app state so the spinner / queue count
        # reflect the authoritative agent state.
        app: "ClawCodexTUI" = self.app  # type: ignore[assignment]
        if hasattr(app, "app_state"):
            self.status_bar.bind_state(app.app_state)
        # Set the initial permission mode on the status bar.
        try:
            from src.permissions.modes import to_external_permission_mode
            ctx = getattr(app, "tool_context", None)
            if ctx is not None and ctx.permission_context is not None:
                mode = to_external_permission_mode(ctx.permission_context.mode or "default")
                self.status_bar.set_permission_mode(mode)
        except Exception:
            pass
        # Attach the live region to the app's announcer so every
        # cross-screen announcement mirrors into this REPL.
        if hasattr(app, "announcer"):
            app.announcer.bind_region(self.live_region)
        self.prompt_input.focus_input()
        self.transcript.append_system(
            "Ready. Type a prompt, or '/' for commands. "
            "Ctrl+D, /exit, or /repl to leave the Textual TUI.",
        )

    # ---- actions ----
    def action_clear_transcript(self) -> None:
        self.transcript.clear_transcript()

    def action_toggle_thinking(self) -> None:
        """Ctrl+T: toggle thinking content visibility in all thinking rows."""
        from src.tui.widgets.messages.assistant_thinking import (
            AssistantThinkingMessage,
        )

        expanded = True
        for row in self.transcript.query(AssistantThinkingMessage):
            row.toggle()
            expanded = row.expanded

        label = "expanded" if expanded else "collapsed"
        self.transcript.append_system(f"Thinking content: {label}", style="muted")

    def action_cycle_permission_mode(self) -> None:
        """Shift+Tab: cycle permission mode. Delegates to app."""
        app: "ClawCodexTUI" = self.app  # type: ignore[assignment]
        if hasattr(app, "action_cycle_permission_mode"):
            app.action_cycle_permission_mode()

    def on_permission_mode_cycle_requested(
        self, _: PermissionModeCycleRequested
    ) -> None:
        """Handle Shift+Tab posted from PromptInput."""
        self.action_cycle_permission_mode()

    # ---- prompt submission ----
    def on_prompt_submitted(self, message: PromptSubmitted) -> None:
        ## _log(f'[repl.py] on_prompt_submitted: {message.text}')
        app: "ClawCodexTUI" = self.app  # type: ignore[assignment]
        text = message.text
        if text.startswith("/"):
            if app.handle_local_slash_command(text, self.transcript):
                return
        self.transcript.append_user(text)
        self.status_bar.set_busy()
        self.status_bar.bump_turn()
        ## _log(f'[repl.py] calling submit_to_agent: {text}')
        app.submit_to_agent(text)

    # ---- agent message handlers ----
    def on_agent_run_started(self, _: AgentRunStarted) -> None:
        self.status_bar.set_busy()

    def on_assistant_chunk(self, message: AssistantChunk) -> None:
        ## _log(f'[repl.py] on_assistant_chunk: {message.text[:50] if message.text else "empty"}...')
        self.transcript.append_assistant_chunk(message.text)

    def on_thinking_chunk(self, message: ThinkingChunk) -> None:
        ## _log(f'[repl.py] on_thinking_chunk: {message.text[:50] if message.text else "empty"}...')
        self.transcript.append_thinking_chunk(message.text)

    def on_assistant_message(self, message: AssistantMessage) -> None:
        ## _log(f'[repl.py] on_assistant_message: {message.text[:100] if message.text else "empty"}...')
        self.transcript.append_assistant(message.text)

    def on_tool_event_message(self, message: ToolEventMessage) -> None:
        self.transcript.append_tool_event(
            kind=message.kind,
            tool_name=message.tool_name,
            tool_input=message.tool_input,
            tool_output=message.tool_output,
            tool_use_id=message.tool_use_id,
            is_error=message.is_error,
            error=message.error,
        )

    def on_advisor_event_message(self, message: AdvisorEventMessage) -> None:
        self.transcript.append_advisor_event(
            kind=message.kind,
            tool_use_id=message.tool_use_id,
            advisor_model=message.advisor_model,
            text=message.text,
            error_code=message.error_code,
        )

    def on_agent_run_finished(self, message: AgentRunFinished) -> None:
        self.status_bar.set_idle()
        if message.error:
            self.transcript.append_system(f"error: {message.error}", style="error")
            app = self.app
            if hasattr(app, "announcer"):
                app.announcer.announce(  # type: ignore[attr-defined]
                    f"Error: {message.error}", level="assertive"
                )
        self.prompt_input.set_enabled(True)
        self.call_after_refresh(self.prompt_input.focus_input)

    # ---- permission modal handlers ----
    def on_permission_requested(self, message: PermissionRequested) -> None:
        app: "ClawCodexTUI" = self.app  # type: ignore[assignment]
        state = getattr(app, "app_state", None)
        if state is None:
            return
        pending = state.pop_next_permission()
        if pending is None or pending.request_id != message.request_id:
            # State drift: search the queue for a matching id.
            for candidate in list(state.pending_permissions):
                if candidate.request_id == message.request_id:
                    pending = candidate
                    break
        if pending is None:
            return
        from .permission_modal import PermissionModal

        self.prompt_input.set_enabled(False)
        if hasattr(app, "announcer"):
            app.announcer.announce(
                f"Permission required: {pending.tool_name}",
                level="assertive",
            )
        app.push_screen(PermissionModal(pending))

    def on_permission_resolved(self, _: PermissionResolved) -> None:
        self.prompt_input.set_enabled(True)
        self.prompt_input.focus_input()

    # ---- AskUserQuestion modal handlers ----
    def on_ask_user_question_requested(
        self, message: AskUserQuestionRequested
    ) -> None:
        app: "ClawCodexTUI" = self.app  # type: ignore[assignment]
        state = getattr(app, "app_state", None)
        if state is None:
            return
        # Find the pending entry by id; fall back to the head of the
        # queue if the worker thread posted the message before the
        # state enqueue (Textual's message pump order is not strict).
        pending = None
        for candidate in list(state.pending_ask_users):
            if candidate.request_id == message.request_id:
                pending = candidate
                break
        if pending is None and state.pending_ask_users:
            pending = state.pending_ask_users[0]
        if pending is None:
            return
        from .ask_user_question import AskUserQuestionModal

        self.prompt_input.set_enabled(False)
        if hasattr(app, "announcer"):
            app.announcer.announce(
                f"Question: {pending.questions[0].get('question', '') if pending.questions else ''}",
                level="assertive",
            )
        app.push_screen(AskUserQuestionModal(pending))

    def on_ask_user_question_resolved(self, _: AskUserQuestionResolved) -> None:
        self.prompt_input.set_enabled(True)
        self.prompt_input.focus_input()

    def on_state_changed(self, _: StateChanged) -> None:
        # Cheap coalesced refresh. The status line has its own timer so
        # there is nothing to do here yet; reserved for Phase 2 widgets
        # (queued-command pill, idle return dialog, etc.).
        pass
