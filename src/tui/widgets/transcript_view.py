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

from collections import deque
from typing import Any

from rich.panel import Panel
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

# Read/search tools whose consecutive DONE rows collapse into one summary
# row (TS CollapsedReadSearchContent / GroupedToolUseContent scope).
# ("ls" deliberately absent: no LS tool registration exists in
# src/tool_system today.)
_READ_GROUP_TOOLS = frozenset({"read", "grep", "glob"})
# Collapse only once a run reaches this many rows (2 keeps short pairs
# visible-but-compact; TS groups any consecutive run in transcript mode).
_READ_GROUP_MIN = 3


class _SnapshotStatic(Static):
    """Static row that participates in the post-exit scrollback dump.

    ``TranscriptView.snapshot()`` skips rows without a ``snapshot``
    attribute — the C3b rows (compact boundary, read-group summary,
    expanded panels) must not vanish from the dump (review M3).
    """

    def __init__(self, renderable: Any, **kwargs: Any) -> None:
        super().__init__(renderable, **kwargs)
        self._snapshot_renderable = renderable

    def snapshot(self) -> Any:
        return self._snapshot_renderable

    def update(self, renderable: Any = "") -> None:  # type: ignore[override]
        self._snapshot_renderable = renderable
        super().update(renderable)

from .messages import (
    AssistantAdvisorMessage,
    AssistantTextMessage,
    AssistantToolUseMessage,
    SystemMessage,
    ToolResultRow,
    UserTextMessage,
)
from .messages.assistant_thinking import AssistantThinkingMessage


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
        # Advisor rows indexed by ``tool_use_id``. Kept separate from
        # ``_tool_rows`` so the eviction sweep can distinguish them and
        # so the lifecycle ("start" → "result") is unambiguous —
        # advisor rows have no surrounding ToolEvent stream the way
        # regular tools do.
        self._advisor_rows: dict[str, AssistantAdvisorMessage] = {}
        # Insertion-ordered list of rows we've mounted. Textual's
        # ``self.children`` lags behind ``mount()`` until the event
        # loop ticks, so we track the order ourselves to make the
        # eviction sweep deterministic (the TS reference pairs
        # ``shouldRenderStatically`` with a snapshot of the message
        # list for the same reason).
        self._mounted_rows: list[Widget] = []
        # C3b ctrl+o: bounded stash of (label, full_content) for rows whose
        # body rendered truncated — mirrors the legacy REPL's
        # ``_expandable_blocks`` (repl/core.py) including the maxlen.
        self._expandables: deque[tuple[str, str]] = deque(maxlen=20)
        # C3b read-group collapse state: rows + labels of the current run
        # of consecutive completed read/search rows, and the summary row
        # that replaces them once the run reaches _READ_GROUP_MIN.
        self._read_rows: list[Widget] = []
        self._read_labels: list[str] = []
        self._read_group_row: Static | None = None
        # Identity of this group's ctrl+o stash entry. Per-read CONTENT
        # stashes land between folds (real Read outputs exceed the
        # truncation limits), so position-based "is it newest?" checks
        # miss — the entry is found by identity wherever it sits
        # (review M2 residual).
        self._read_group_entry: tuple[str, str] | None = None
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
        self._break_read_group()
        row = UserTextMessage(text)
        self.mount(row)
        self._scroll_end()

    def append_assistant_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        # Symmetric guard with ``append_thinking_chunk`` (Critic-flagged):
        # if the active row is a thinking widget, retire it before
        # mounting an assistant text row. Otherwise assistant chunks
        # would be appended into the thinking row's body — visible bug
        # the moment Phase-12 dispatch wiring lands.
        active = self._active_assistant
        if active is not None and not isinstance(active, AssistantTextMessage):
            self._retire_active_assistant()
        if self._active_assistant is None:
            self._break_read_group()
            self._active_assistant = AssistantTextMessage()
            self.mount(self._active_assistant)
        self._active_assistant.append_chunk(chunk)
        self._scroll_end()

    # Phase-12 (gap #16 sub-item): widget-and-helper shell for
    # thinking-block content. The :class:`AssistantThinkingMessage`
    # widget exists; the methods below mount and stream into it
    # correctly. **Dispatch wiring is deferred** — the agent loop's
    # ``on_text_chunk`` callback only emits user-visible text, not
    # ``ThinkingBlock`` content blocks. Adding ``on_thinking_chunk`` is
    # an agent-loop change that lives in a separate ticket so the loop
    # API change can be reviewed in isolation. Until then, callers
    # invoke these helpers directly (a few tests do; no production
    # caller does), and the symmetric guard above keeps the transition
    # logic correct ahead of the wiring.
    def append_thinking_chunk(
        self, chunk: str, *, redacted: bool = False
    ) -> None:
        if not chunk:
            return
        # We re-use ``_active_assistant`` for thinking too; the
        # transition to a real assistant text turn calls
        # :meth:`_retire_active_assistant` which finalises whichever
        # row is active.
        active = self._active_assistant
        if not isinstance(active, AssistantThinkingMessage) or (
            active.has_class("-redacted") != redacted
        ):
            self._retire_active_assistant()
            self._break_read_group()
            row = AssistantThinkingMessage(redacted=redacted)
            self._active_assistant = row  # type: ignore[assignment]
            self.mount(row)
            active = row
        active.append_chunk(chunk)
        self._scroll_end()

    def append_thinking(self, text: str, *, redacted: bool = False) -> None:
        active = self._active_assistant
        if isinstance(active, AssistantThinkingMessage):
            active.finalise(text)
            self._active_assistant = None
        elif (text or "").strip():
            self._break_read_group()
            row = AssistantThinkingMessage(redacted=redacted)
            self.mount(row)
            row.finalise(text)
        self._scroll_end()

    def append_assistant(self, text: str) -> None:
        if self._active_assistant is not None:
            self._active_assistant.finalise(text)
            self._active_assistant = None
        elif (text or "").strip():
            self._break_read_group()
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
            if (tool_name or "").lower() not in _READ_GROUP_TOOLS:
                self._break_read_group()
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
                # PRODUCTION SHAPE: result events arrive with
                # tool_name="" (agent_loop_compat.py builds them without
                # a name) — the row, captured at tool_use time, is the
                # authoritative source (review B1).
                effective_name = (
                    getattr(row, "tool_name", "") or tool_name or ""
                )
                # C3b ctrl+o: stash full output for anything the row's
                # panel will truncate (limits mirror
                # tool_activity.base.truncated_panel). Matched rows only
                # — the standalone fallback below renders untruncated.
                if isinstance(tool_output, str) and tool_output:
                    from .tool_activity.base import (
                        _BODY_MAX_CHARS,
                        _BODY_MAX_LINES,
                    )

                    if (
                        len(tool_output) > _BODY_MAX_CHARS
                        or tool_output.count("\n") + 1 > _BODY_MAX_LINES
                    ):
                        self.note_expandable(
                            f"{effective_name or 'tool'} result", tool_output
                        )
                if is_error:
                    row.mark_error(tool_output)
                    self._break_read_group()
                else:
                    row.mark_done(tool_output)
                    if effective_name.lower() in _READ_GROUP_TOOLS:
                        self._on_read_tool_done(row, effective_name)
                self._scroll_end()
                return
            # No matching tool_use row; emit a standalone result row so
            # the event is not silently dropped.
            try:
                from src.tool_system.renderers import summarize_tool_result

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
            self._break_read_group()
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

    def append_advisor_event(
        self,
        *,
        kind: str,
        tool_use_id: str,
        advisor_model: str | None = None,
        text: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Add or update an advisor row.

        ``kind="start"`` mounts a fresh row in the in-progress state.
        ``kind="result"`` looks up the row by ``tool_use_id`` and swaps
        it into the done/error terminal state. If we see a result
        without a prior start (e.g. the bridge inspects history after a
        resume where the start landed before the UI was up), we mount
        the row directly in the terminal state so the event isn't
        silently dropped.
        """
        self._retire_active_assistant()
        self._break_read_group()
        if kind == "start":
            if tool_use_id in self._advisor_rows:
                return
            row = AssistantAdvisorMessage(
                tool_use_id=tool_use_id, advisor_model=advisor_model
            )
            self._advisor_rows[tool_use_id] = row
            self.mount(row)
            row.mark_running()
            self._scroll_end()
            return
        if kind == "result":
            row = self._advisor_rows.pop(tool_use_id, None)
            if row is None:
                # Result without a start — mount fresh and finalise.
                row = AssistantAdvisorMessage(
                    tool_use_id=tool_use_id, advisor_model=advisor_model
                )
                self.mount(row)
            if error_code:
                row.mark_error(error_code)
            else:
                row.mark_done(text)
            self._scroll_end()
            return
        # Unknown kind — surface as a muted system row.
        self.mount(SystemMessage(f"advisor [{kind}]", style="muted"))
        self._scroll_end()

    def append_system(self, text: str, *, style: str = "muted") -> None:
        """Append a system / informational row.

        The ``style`` argument historically accepted Rich style strings
        (e.g. ``"dim"``, ``"red"``, ``"cyan"``); Phase 1 maps the most
        common ones to the semantic variants exposed by
        :class:`SystemMessage`.
        """

        self._retire_active_assistant()
        self._break_read_group()
        canonical = _canonical_system_style(style)
        self.mount(SystemMessage(text, style=canonical))
        self._scroll_end()

    def append_compact_boundary(self, text: str) -> None:
        """Distinct rule-style row marking a conversation compaction
        (TS CompactBoundaryMessage / CompactSummary)."""

        self._retire_active_assistant()
        self._break_read_group()
        row = _SnapshotStatic(
            Text(f"── ✻ {text} ──", style="bold dim"),
            classes="compact-boundary",
            markup=False,
        )
        self.mount(row)
        self._scroll_end()

    # ---- C3b ctrl+o expandables ------------------------------------
    def note_expandable(self, label: str, full_text: str) -> None:
        """Stash full content for a row that rendered truncated."""

        if full_text:
            self._expandables.append((label, full_text))

    def expand_last(self) -> None:
        """Re-print the most recent truncated block in full, as a fresh
        row below (legacy-REPL ``_do_expand_last`` parity — the entry is
        NOT popped, so repeated ctrl+o re-prints the same newest block
        until a newer truncated row arrives)."""

        if not self._expandables:
            self.append_system("Nothing to expand.", style="muted")
            return
        label, full_text = self._expandables[-1]
        self._retire_active_assistant()
        self._break_read_group()
        self.mount(
            _SnapshotStatic(
                Panel(
                    Text(full_text),
                    title=f"expanded: {label}",
                    border_style="bright_black",
                    padding=(0, 1),
                ),
                markup=False,
            )
        )
        self._scroll_end()

    # ---- C3b read-group collapse -------------------------------------
    def _break_read_group(self) -> None:
        self._read_rows = []
        self._read_labels = []
        self._read_group_row = None
        self._read_group_entry = None

    def _read_label(self, row: Widget, fallback_name: str) -> str:
        tool_input = getattr(row, "tool_input", None) or {}
        arg = (
            tool_input.get("file_path")
            or tool_input.get("pattern")
            or tool_input.get("path")
            or ""
        )
        name = getattr(row, "tool_name", "") or fallback_name or "tool"
        return f"{name}({arg})" if arg else str(name)

    def _remove_row(self, row: Widget) -> None:
        try:
            self._mounted_rows.remove(row)
        except ValueError:
            pass
        try:
            row.remove()
        except Exception:
            pass

    def _on_read_tool_done(self, row: Widget, tool_name: str) -> None:
        """Fold runs of completed read/search rows into one summary row.

        The run accumulates silently until ``_READ_GROUP_MIN``; from then
        on the individual rows are removed and a single summary row shows
        the aggregated labels (full list reachable via ctrl+o — ONE stash
        entry per group, updated in place across folds).
        """

        self._read_rows.append(row)
        self._read_labels.append(self._read_label(row, tool_name))
        if len(self._read_labels) < _READ_GROUP_MIN:
            return
        labels = self._read_labels
        shown = labels[-4:]
        prefix = "… , " if len(labels) > 4 else ""
        summary = Text(
            f"⌕ {len(labels)} reads/searches · "
            + prefix
            + ", ".join(shown)
            + "  (ctrl+o for the full list)",
            style="dim",
        )
        if self._read_group_row is None:
            group = _SnapshotStatic(summary, classes="read-group", markup=False)
            self.mount(group)
            self._read_group_row = group
        else:
            self._read_group_row.update(summary)
        for run_row in self._read_rows:
            self._remove_row(run_row)
        self._read_rows = []
        stash_entry = ("collapsed reads/searches", "\n".join(labels))
        # Replace THIS group's previous entry by identity, wherever it
        # sits — per-read content stashes interleave between folds, so the
        # entry is rarely the newest. Evicted (not found) → append fresh.
        replaced = False
        if self._read_group_entry is not None:
            for i in range(len(self._expandables) - 1, -1, -1):
                if self._expandables[i] is self._read_group_entry:
                    self._expandables[i] = stash_entry
                    replaced = True
                    break
        if not replaced:
            self._expandables.append(stash_entry)
        self._read_group_entry = stash_entry
        self._scroll_end()

    def clear_transcript(self) -> None:
        self._active_assistant = None
        self._tool_rows.clear()
        self._advisor_rows.clear()
        self._mounted_rows.clear()
        self._expandables.clear()
        self._break_read_group()
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
            if (
                isinstance(row, AssistantAdvisorMessage)
                and row.tool_use_id in self._advisor_rows
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
