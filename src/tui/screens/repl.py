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
    AdvisorEventMessage,
    AgentRunFinished,
    AgentRunStarted,
    AssistantChunk,
    AssistantMessage,
    PermissionRequested,
    PermissionResolved,
    QueuedPromptReady,
    QueuedPromptsChanged,
    StateChanged,
    ToolEventMessage,
)
from ..a11y import LiveRegion, aria_label
from ..widgets.fullscreen_layout import FullscreenLayout
from ..widgets.header import StartupHeader
from ..widgets.prompt_input import PromptInput, PromptSubmitted
from ..widgets.queued_commands import QueuedCommands
from ..widgets.status_line import StatusLine
from ..widgets.transcript_view import Transcript

if TYPE_CHECKING:  # pragma: no cover
    from ..app import ClawCodexTUI
    from ..commands import CommandSuggestion


class REPLScreen(Screen):
    """Composes the interactive TUI layout."""

    BINDINGS = [
        ("ctrl+l", "clear_transcript", "Clear transcript"),
        # C3b: legacy-REPL parity — re-print the most recent truncated
        # block in full (CtrlOToExpand).
        ("ctrl+o", "expand_last", "Expand last truncated"),
        # TS history:search (defaultBindings ctrl+r) — opens the existing
        # reverse-history-search dialog (was reachable only via /history).
        ("ctrl+r", "history_search", "Search history"),
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
        self._context_low_warned = False
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
        # /vim: seed the editor mode from the persisted editorMode config key
        # (best-effort; the command toggles it, effective on next launch).
        from src.command_system.vim_command import initial_vim_mode

        self.prompt_input = PromptInput(
            words_provider=words_provider,
            suggestions_provider=suggestions_provider,
            files_provider=self._workspace_files,
            vim_mode=initial_vim_mode(),
        )
        # Dim preview of prompts queued while a run is in flight (parity
        # with PromptInputQueuedCommands). Mounted directly above the
        # prompt input; hidden while the queue is empty.
        self.queued_commands = QueuedCommands()
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

    def _workspace_files(self) -> list[str]:
        """Cached workspace file list for the ``@``-mention dropdown.

        ``rg --files`` is run once per session and reused (same snapshot
        strategy as the ``/open`` quick-open dialog) so typing ``@`` never
        spawns ripgrep per keystroke. Failures degrade to an empty list.
        """

        cached = getattr(self, "_workspace_files_cache", None)
        if cached is not None:
            return cached
        try:
            from src.services.workspace_search import list_workspace_files

            files, _truncated = list_workspace_files(str(self._workspace_root))
        except Exception:
            # Transient ripgrep failure — don't cache, so the next `@`
            # keystroke retries (TS resets its file-list promise on error).
            return []
        # Cache successful results (including a legitimately empty repo) so
        # `@` typing never re-spawns ripgrep per keystroke.
        self._workspace_files_cache = files
        return files

    def on_mount(self) -> None:
        self._fullscreen.scroll_region().mount(self.header_widget)
        self._fullscreen.scroll_region().mount(self.transcript)
        self._fullscreen.bottom_region().mount(self.live_region)
        self._fullscreen.bottom_region().mount(self.status_bar)
        # Order: live region, status line, queued preview, prompt — so
        # the queued text sits right above the input (TS marginTop), with
        # the at-a-glance count staying on the status line.
        self._fullscreen.bottom_region().mount(self.queued_commands)
        self._fullscreen.bottom_region().mount(self.prompt_input)
        # Bind the status line to app state so the spinner / queue count
        # reflect the authoritative agent state.
        app: "ClawCodexTUI" = self.app  # type: ignore[assignment]
        if hasattr(app, "app_state"):
            self.status_bar.bind_state(app.app_state)
        # Drive the footer's "esc to interrupt" hint off the same busy
        # signal as the spinner (single source of truth).
        self.status_bar.bind_footer(self.prompt_input.footer)
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

    def action_expand_last(self) -> None:
        self.transcript.expand_last()

    def action_history_search(self) -> None:
        app = self.app
        opener = getattr(app, "_open_history_search", None)
        if callable(opener):
            opener(self.transcript)

    # ---- prompt submission ----
    def on_prompt_submitted(self, message: PromptSubmitted) -> None:
        app: "ClawCodexTUI" = self.app  # type: ignore[assignment]
        text = message.text
        if text.startswith("/"):
            if app.handle_local_slash_command(text, self.transcript):
                return
        if text.startswith("!"):
            # C4 bash-mode: direct execution, no agent turn, no busy
            # spinner (the echo row mounts synchronously; the worker
            # fills in the output when the command finishes).
            app.run_bash_mode(text[1:], self.transcript)
            return
        if text.startswith("#") and text[1:].strip():
            # C9 memory shortcut: pick a memory file, append the note,
            # no agent turn. A bare "#" falls through to the agent.
            app.run_memory_shortcut(text[1:], self.transcript)
            return
        # Plain agent prompt. ``submit_to_agent`` returns False when the
        # bridge is busy and the prompt was QUEUED — in that case it
        # shows only in the dim queued-prompts preview (refreshed via
        # QueuedPromptsChanged), NOT the transcript, until the drain runs
        # it (TS parity: queued commands live above the input, not in the
        # conversation). Only render the user row + bump the turn when a
        # run actually started.
        if app.submit_to_agent(text):
            self.transcript.append_user(text)
            self.status_bar.set_busy()
            self.status_bar.bump_turn()

    # ---- queued-prompt drain (TS useCommandQueue auto-processing) ----
    def on_queued_prompt_ready(self, _: QueuedPromptReady) -> None:
        """Drain the oldest queued prompt now that the bridge is idle.

        Posted by ``AgentBridge._finish`` after a run ends. We re-check
        idle + non-empty on the UI thread (authoritative — the worker
        side is only a filter), pop the oldest prompt, and replay it like
        a plain prompt. ``record_history=False`` because it was recorded
        when first typed. One pop per ``QueuedPromptReady`` → FIFO, one
        prompt per turn (the next run's ``_finish`` posts again if more
        remain).
        """

        app = self.app
        bridge = getattr(app, "_agent_bridge", None)
        state = getattr(app, "app_state", None)
        if bridge is None or state is None:
            return
        # A spurious post (queue cleared by ESC, or a run started in the
        # meantime) must be a safe no-op.
        if bridge.busy or not state.queued_prompts:
            return
        text = state.queued_prompts.pop(0)
        self._refresh_queued_preview()
        # We just confirmed the bridge idle on this single UI thread, so
        # the submit starts a run (returns True). The defensive ``else``
        # only matters if that invariant is ever broken — then the bridge
        # re-queued it under its lock and it drains on the next turn.
        if app.submit_to_agent(text, record_history=False):
            self.transcript.append_user(text)
            self.status_bar.set_busy()
            self.status_bar.bump_turn()
        else:  # pragma: no cover - unreachable on the single UI thread
            self._refresh_queued_preview()

    def pop_queue_into_input(self) -> None:
        """ESC Priority 2: drain queued prompts back into the prompt input.

        TS ``popAllEditable`` (``messageQueueManager.ts:428``): the queued
        texts (in order) are joined with the current draft by newlines and
        loaded into the input for editing; the queue is then emptied.
        Prompts are moved INTO the input, never discarded — so ESC can
        never lose what the user typed. (Images / cursor-offset handling
        in TS are N-A for the plain-``str`` Python queue.)

        Caveat: the prompt input is still single-line (Textual ``Input``;
        a ``TextArea`` swap is Phase-2 — see ``prompt_input.py``). A merged
        value containing ``\\n`` (only when there are multiple queued
        prompts, or a non-empty draft) is preserved verbatim in the value
        (correct on submit) but renders flat until that swap.
        """

        app = self.app
        state = getattr(app, "app_state", None)
        if state is None or not state.queued_prompts:
            return
        queued = list(state.queued_prompts)
        current = self.prompt_input.current_text()
        merged = "\n".join(t for t in [*queued, current] if t)
        state.queued_prompts.clear()
        self.prompt_input.set_value(merged)
        self.prompt_input.focus_input()
        self._refresh_queued_preview()

    def on_queued_prompts_changed(self, _: QueuedPromptsChanged) -> None:
        self._refresh_queued_preview()

    def _refresh_queued_preview(self) -> None:
        """Rebuild the dim queued-prompts widget from live app state."""

        state = getattr(self.app, "app_state", None)
        prompts = list(getattr(state, "queued_prompts", []) or [])
        self.queued_commands.set_prompts(prompts)

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
        self._maybe_warn_context_low()
        # C3a event-driven statusline refresh (replaces hot polling): the
        # data the user's command renders changes when a run finishes.
        try:
            self.status_bar.refresh_custom_status()
        except Exception:
            pass
        self.prompt_input.focus_input()

    def _maybe_warn_context_low(self) -> None:
        """C3a: one transcript warning per crossing of the TS context-low
        threshold (TokenWarning.tsx); re-arms when usage drops back below
        (e.g. after /compact or /clear)."""

        app = self.app
        state = getattr(app, "app_state", None)
        tokens = int(getattr(state, "last_turn_input_tokens", 0) or 0)
        if not tokens:
            return
        try:
            from src.services.token_warning import (
                calculate_token_warning_state,
                context_low_message,
            )

            tw = calculate_token_warning_state(
                tokens, getattr(state, "model", "") or ""
            )
        except Exception:
            return
        if tw.is_above_warning and not self._context_low_warned:
            self._context_low_warned = True
            # TS colors warning vs error distinctly; the buffers are equal
            # today so error is the live branch — the fallback exists for
            # the day TS diverges them again.
            self.transcript.append_system(
                context_low_message(tw),
                style="error" if tw.is_above_error else "warning",
            )
        elif not tw.is_above_warning:
            self._context_low_warned = False

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
