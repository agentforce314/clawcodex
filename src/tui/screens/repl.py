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

from pathlib import Path
from typing import Callable, TYPE_CHECKING

from textual.app import ComposeResult
from textual.screen import Screen

from ..messages import (
    AgentRunFinished,
    AgentRunStarted,
    AssistantChunk,
    AssistantMessage,
    PermissionRequested,
    PermissionResolved,
    StateChanged,
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


class REPLScreen(Screen):
    """Composes the interactive TUI layout."""

    BINDINGS = [
        ("ctrl+l", "clear_transcript", "Clear transcript"),
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
    ) -> None:
        super().__init__()
        self._version = version
        self._provider = provider
        self._model = model
        self._workspace_root = Path(workspace_root)
        self._words_provider = words_provider

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
        )
        self.prompt_input = PromptInput(words_provider=words_provider)
        # ARIA live region — stays height: 1 and only announces the
        # most recent status change. Mounted just above the status
        # bar so it's adjacent to the prompt for single-sweep reads.
        self.live_region = LiveRegion(aria_label="Status")
        aria_label(self.transcript, "Conversation transcript")
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

    # ---- prompt submission ----
    def on_prompt_submitted(self, message: PromptSubmitted) -> None:
        app: "ClawCodexTUI" = self.app  # type: ignore[assignment]
        text = message.text
        if text.startswith("/"):
            if app.handle_local_slash_command(text, self.transcript):
                return
        self.transcript.append_user(text)
        self.status_bar.set_busy()
        self.status_bar.bump_turn()
        app.submit_to_agent(text)

    # ---- agent message handlers ----
    def on_agent_run_started(self, _: AgentRunStarted) -> None:
        self.status_bar.set_busy()

    def on_assistant_chunk(self, message: AssistantChunk) -> None:
        self.transcript.append_assistant_chunk(message.text)

    def on_assistant_message(self, message: AssistantMessage) -> None:
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

    def on_agent_run_finished(self, message: AgentRunFinished) -> None:
        self.status_bar.set_idle()
        if message.error:
            self.transcript.append_system(f"error: {message.error}", style="error")
            app = self.app
            if hasattr(app, "announcer"):
                app.announcer.announce(  # type: ignore[attr-defined]
                    f"Error: {message.error}", level="assertive"
                )
        self.prompt_input.focus_input()

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

    def on_state_changed(self, _: StateChanged) -> None:
        # Cheap coalesced refresh. The status line has its own timer so
        # there is nothing to do here yet; reserved for Phase 2 widgets
        # (queued-command pill, idle return dialog, etc.).
        pass
