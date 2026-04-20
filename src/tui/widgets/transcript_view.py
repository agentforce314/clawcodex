"""Scrollable transcript view.

Hosts the message widgets from :mod:`src.tui.widgets.messages` in a
``VerticalScroll`` so rows can mutate in place (streaming text, tool
activity status) without disturbing the user's scroll position — the
key behavioural parity target versus the Phase 0 :class:`Transcript`
widget which used ``RichLog`` and could only append text.

Additionally exposes a compatibility layer that mimics the Phase 0
``Transcript`` API (``append_user``, ``append_assistant_chunk``,
``append_assistant``, ``append_tool_event``, ``append_system``,
``clear_transcript``) so existing callers — notably the legacy
:class:`REPLScreen` tests and the Rich-REPL handoff path — keep
working. The compatibility shim constructs the richer widget types so
the visual output matches the new transcript regardless of entry point.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from .messages import (
    AssistantTextMessage,
    AssistantToolUseMessage,
    SystemMessage,
    ToolResultRow,
    UserTextMessage,
)


class TranscriptView(VerticalScroll):
    """Live-updating scrollable transcript."""

    DEFAULT_CSS = """
    TranscriptView {
        padding: 1 1 0 1;
        height: 1fr;
        scrollbar-size: 1 1;
    }
    """

    # Soft cap on the number of transcript rows we keep mounted. Long
    # sessions can otherwise accumulate thousands of widgets and slow
    # down Textual's render pipeline; this approximates the TS
    # ``shouldRenderStatically`` + static-cache optimisation by just
    # evicting the oldest completed rows when we exceed the cap.
    max_messages: int = 500

    def __init__(self, *, max_messages: int | None = None) -> None:
        super().__init__()
        # The active streaming assistant row; held as a direct reference
        # so streaming chunks don't need to re-query the DOM every time.
        self._active_assistant: AssistantTextMessage | None = None
        # Tool-use rows indexed by ``tool_use_id`` so ``tool_result`` /
        # ``tool_error`` events can find the matching activity widget.
        self._tool_rows: dict[str, AssistantToolUseMessage] = {}
        # Insertion-ordered list of rows we've mounted. Textual's
        # ``self.children`` lags behind ``mount()`` until the event
        # loop ticks, so we track the order ourselves to make the
        # eviction sweep deterministic (the TS reference pairs
        # ``shouldRenderStatically`` with a snapshot of the message
        # list for the same reason).
        self._mounted_rows: list[Widget] = []
        if max_messages is not None:
            self.max_messages = max(1, int(max_messages))

    def mount(self, *widgets: Widget, **kwargs: Any):  # type: ignore[override]
        """Track each mounted row in :attr:`_mounted_rows`.

        Overriding ``mount`` keeps the row-order record in sync no
        matter which helper (``append_user``/``append_tool_event``/…)
        called it.
        """

        for w in widgets:
            self._mounted_rows.append(w)
        return super().mount(*widgets, **kwargs)

    # ---- public API (Phase 0 compatibility) -----------------------
    def append_user(self, text: str) -> None:
        self._retire_active_assistant()
        row = UserTextMessage(text)
        self.mount(row)
        self._scroll_end()

    def append_assistant_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        if self._active_assistant is None:
            self._active_assistant = AssistantTextMessage()
            self.mount(self._active_assistant)
        self._active_assistant.append_chunk(chunk)
        self._scroll_end()

    def append_assistant(self, text: str) -> None:
        if self._active_assistant is not None:
            self._active_assistant.finalise(text)
            self._active_assistant = None
        elif (text or "").strip():
            row = AssistantTextMessage()
            self.mount(row)
            row.finalise(text)
        self._scroll_end()

    def append_tool_event(
        self,
        *,
        kind: str,
        tool_name: str,
        tool_input: dict[str, Any] | None,
        tool_output: Any,
        is_error: bool,
        error: str | None,
        tool_use_id: str | None = None,
    ) -> None:
        self._retire_active_assistant()
        key = tool_use_id or _synthetic_id(tool_name, tool_input)

        if kind == "tool_use":
            row = AssistantToolUseMessage(
                tool_use_id=key,
                tool_name=tool_name,
                tool_input=tool_input or {},
            )
            self._tool_rows[key] = row
            self.mount(row)
            row.mark_running()
            self._scroll_end()
            return

        if kind == "tool_result":
            row = self._tool_rows.pop(key, None)
            if row is not None:
                if is_error:
                    row.mark_error(tool_output)
                else:
                    row.mark_done(tool_output)
                self._scroll_end()
                return
            # No matching tool_use row; emit a standalone result row so
            # the event is not silently dropped.
            try:
                from src.tool_system.agent_loop import summarize_tool_result

                summary = summarize_tool_result(tool_name, tool_output) or tool_name
            except Exception:
                summary = tool_name
            body = None
            if isinstance(tool_output, str):
                body = tool_output
            self.mount(
                ToolResultRow(
                    tool_name=tool_name,
                    summary=summary,
                    body=body,
                    is_error=is_error,
                )
            )
            self._scroll_end()
            return

        if kind == "tool_error":
            row = self._tool_rows.pop(key, None)
            if row is not None:
                row.mark_error(tool_output, error=error)
                self._scroll_end()
                return
            self.mount(
                ToolResultRow(
                    tool_name=tool_name,
                    summary=f"{tool_name}: {error or 'error'}",
                    is_error=True,
                )
            )
            self._scroll_end()
            return

        # Unknown kind — surface as a muted system row so the event
        # doesn't disappear.
        self.mount(SystemMessage(f"{tool_name} [{kind}]", style="muted"))
        self._scroll_end()

    def append_system(self, text: str, *, style: str = "muted") -> None:
        """Append a system / informational row.

        The ``style`` argument historically accepted Rich style strings
        (e.g. ``"dim"``, ``"red"``, ``"cyan"``); Phase 1 maps the most
        common ones to the semantic variants exposed by
        :class:`SystemMessage`.
        """

        self._retire_active_assistant()
        canonical = _canonical_system_style(style)
        self.mount(SystemMessage(text, style=canonical))
        self._scroll_end()

    def clear_transcript(self) -> None:
        self._active_assistant = None
        self._tool_rows.clear()
        self._mounted_rows.clear()
        try:
            for child in list(self.children):
                child.remove()
        except Exception:
            pass

    # ---- diagnostic helpers ----
    @property
    def message_count(self) -> int:
        return len(self._mounted_rows)

    # ---- post-exit snapshot -------------------------------------------
    def snapshot(self) -> list[Any]:
        """Return an ordered list of Rich renderables for the transcript.

        Used by :class:`ClawCodexTUI` right before it calls
        :meth:`App.exit` to dump the transcript to the parent terminal's
        scrollback buffer. Matching the ink reference's non-fullscreen
        behaviour, the content the user saw while the app was running
        stays visible after the TUI tears down.
        """

        renderables: list[Any] = []
        for row in self._mounted_rows:
            fn = getattr(row, "snapshot", None)
            if fn is None:
                continue
            try:
                piece = fn()
            except Exception:
                continue
            if piece is None:
                continue
            if isinstance(piece, tuple):
                renderables.extend(piece)
            else:
                renderables.append(piece)
        return renderables

    # ---- internals ----
    def _retire_active_assistant(self) -> None:
        """Finalise any in-progress streaming row so subsequent rows are
        not mistakenly treated as continuation of the assistant turn.
        """

        if self._active_assistant is not None:
            self._active_assistant.finalise(self._active_assistant.streaming_text)
            self._active_assistant = None

    def _scroll_end(self) -> None:
        self._evict_overflow()
        try:
            self.scroll_end(animate=False)
        except Exception:
            pass

    def _evict_overflow(self) -> None:
        """Drop the oldest fully-completed rows once we exceed the cap.

        We keep the active streaming row and any in-flight tool rows
        (they're still mutating) regardless of age, because evicting a
        streaming row mid-turn would strand its updates.
        """

        if len(self._mounted_rows) <= self.max_messages:
            return
        overflow = len(self._mounted_rows) - self.max_messages
        evicted = 0
        idx = 0
        while evicted < overflow and idx < len(self._mounted_rows):
            row = self._mounted_rows[idx]
            if row is self._active_assistant:
                idx += 1
                continue
            if (
                isinstance(row, AssistantToolUseMessage)
                and row.tool_use_id in self._tool_rows
            ):
                idx += 1
                continue
            try:
                row.remove()
            except Exception:
                pass
            self._mounted_rows.pop(idx)
            evicted += 1


def _synthetic_id(tool_name: str, tool_input: dict[str, Any] | None) -> str:
    """Stable id used when the tool event is missing ``tool_use_id``."""
    base = tool_name or "tool"
    if tool_input:
        try:
            return f"{base}:{hash(frozenset((k, str(v)) for k, v in tool_input.items()))}"
        except Exception:
            return f"{base}:anon"
    return f"{base}:anon"


def _canonical_system_style(style: str) -> str:
    if not style:
        return "muted"
    key = style.strip().lower()
    if key in ("red", "error", "danger"):
        return "error"
    return "muted"


# ---------- Backward-compatible alias ----------
#
# The public widget surface from Phase 0 was :class:`Transcript` (a
# ``RichLog`` subclass). We keep the name as an alias for now so in-tree
# tests and the Rich REPL handoff continue to import from
# :mod:`src.tui.widgets` — the structural differences (RichLog vs
# VerticalScroll) are intentionally hidden behind the Phase 0 API.
class Transcript(TranscriptView):
    """Alias of :class:`TranscriptView` — kept for Phase 0 callers."""

    @property
    def lines(self) -> list[Widget]:
        """Phase 0 compatibility: tests used ``len(transcript.lines)`` to
        assert the widget grew. Return the list of message-row children
        so the length grows by one per :meth:`append_*` call.
        """

        return list(self.children)
