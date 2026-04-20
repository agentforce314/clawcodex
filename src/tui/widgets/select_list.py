"""Reusable keyboard-first select list.

Mirrors the behavioural contract of ``components/Select.tsx`` in the
TypeScript Ink reference: arrow keys or ``j``/``k`` move the cursor,
``Enter`` selects, ``Esc`` cancels (only if ``allow_cancel`` is
``True``). Options may carry an arbitrary payload (``value``) so the
caller can hang richer data off each row without embedding it in the
rendered label.

The widget emits three message types — :class:`OptionHighlighted`,
:class:`OptionSelected`, and :class:`SelectionCancelled` — so callers
can wire it into any surrounding screen (modal or inline) without
subclassing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from ..a11y import describe_option


@dataclass
class SelectOption:
    """A single row in :class:`SelectList`.

    ``label`` is what the user sees; ``value`` is returned to the
    caller on select (defaults to ``label`` if not provided).
    ``description`` is shown dim on the same row, matching the
    ``label — description`` layout used across the ink reference.
    """

    label: str
    value: Any = None
    description: str | None = None
    disabled: bool = False

    def __post_init__(self) -> None:
        if self.value is None:
            self.value = self.label


class SelectList(Static):
    """Keyboard-navigable list of :class:`SelectOption`.

    Phase 2: deliberately simple — no scrolling, no groups, no async
    loading. Dialogs that need any of those (HistorySearch, LogSelector)
    compose a :class:`SelectList` inside a richer screen.
    """

    DEFAULT_CSS = """
    SelectList {
        height: auto;
        width: 100%;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("up", "move(-1)", "Previous", show=False),
        Binding("down", "move(1)", "Next", show=False),
        Binding("k", "move(-1)", "Previous", show=False),
        Binding("j", "move(1)", "Next", show=False),
        Binding("ctrl+p", "move(-1)", "Previous", show=False),
        Binding("ctrl+n", "move(1)", "Next", show=False),
        Binding("home", "move_to_edge(-1)", "First", show=False),
        Binding("end", "move_to_edge(1)", "Last", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    # ``init=False`` + explicit mount-time assignment avoids the
    # ``ReactiveError`` Textual raises when a reactive is touched
    # before the widget is attached to a running app (tests boot the
    # widget outside of :class:`App.run_test`).
    cursor: reactive[int] = reactive(0, init=False)

    # ---- messages ----
    class OptionHighlighted(Message):
        def __init__(self, index: int, option: SelectOption) -> None:
            super().__init__()
            self.index = index
            self.option = option

    class OptionSelected(Message):
        def __init__(self, index: int, option: SelectOption) -> None:
            super().__init__()
            self.index = index
            self.option = option

    class SelectionCancelled(Message):
        pass

    # ---- construction ----
    def __init__(
        self,
        options: list[SelectOption],
        *,
        initial_index: int = 0,
        allow_cancel: bool = True,
    ) -> None:
        self._options: list[SelectOption] = list(options)
        self._allow_cancel = allow_cancel
        self._initial_index = (
            max(0, min(initial_index, len(self._options) - 1))
            if self._options
            else 0
        )
        super().__init__(self._render_text(self._initial_index), markup=False)
        self.can_focus = True

    def on_mount(self) -> None:
        # Assign the reactive only after we're attached to the app so
        # the reactive machinery has a running event loop.
        self.cursor = self._initial_index

    # ---- mutation ----
    def set_options(self, options: list[SelectOption], *, keep_cursor: bool = False) -> None:
        self._options = list(options)
        new_cursor = self.cursor if keep_cursor else 0
        self.cursor = max(0, min(new_cursor, len(self._options) - 1)) if self._options else 0
        self.update(self._render_text(self.cursor))

    @property
    def options(self) -> list[SelectOption]:
        return list(self._options)

    @property
    def current(self) -> SelectOption | None:
        if not self._options:
            return None
        return self._options[self.cursor]

    # ---- actions ----
    def action_move(self, delta: int) -> None:
        if not self._options:
            return
        new_index = (self.cursor + delta) % len(self._options)
        # Skip disabled rows.
        for _ in range(len(self._options)):
            if not self._options[new_index].disabled:
                break
            new_index = (new_index + delta) % len(self._options)
        self.cursor = new_index

    def action_move_to_edge(self, direction: int) -> None:
        if not self._options:
            return
        self.cursor = len(self._options) - 1 if direction > 0 else 0

    def action_select(self) -> None:
        if not self._options:
            return
        option = self._options[self.cursor]
        if option.disabled:
            return
        self.post_message(self.OptionSelected(self.cursor, option))

    def action_cancel(self) -> None:
        if not self._allow_cancel:
            return
        self.post_message(self.SelectionCancelled())

    # ---- rendering ----
    def watch_cursor(self, value: int) -> None:
        self.update(self._render_text(value))
        if self._options and 0 <= value < len(self._options):
            self.post_message(self.OptionHighlighted(value, self._options[value]))

    def _render_text(self, cursor: int) -> Text:
        if not self._options:
            return Text("(no options)", style="dim")
        out = Text()
        for idx, opt in enumerate(self._options):
            is_cursor = idx == cursor
            marker = "❯ " if is_cursor else "  "
            line = Text(marker, style="bold cyan" if is_cursor else "")
            style = "bold" if is_cursor else ""
            # Prefix disabled rows with a text marker so screen readers
            # announce the state in addition to the dim/strike styling,
            # which is invisible to AT. ``describe_option`` centralises
            # the prefix format.
            if opt.disabled:
                style = "dim strike"
                line.append("[disabled] ", style="dim")
            line.append(opt.label, style=style)
            if opt.description:
                line.append("  ")
                line.append(opt.description, style="dim")
            if idx < len(self._options) - 1:
                line.append("\n")
            out.append(line)
        return out

    # ---- accessibility ----
    def describe(self) -> list[str]:
        """Return the list rendered as a11y-friendly plain strings.

        Intended for screen-reader hand-offs and tests; callers that
        need the Rich :class:`Text` should use :meth:`_render_text`.
        """

        lines: list[str] = []
        for idx, opt in enumerate(self._options):
            lines.append(
                describe_option(
                    opt.label,
                    disabled=opt.disabled,
                    selected=idx == self.cursor,
                    description=opt.description,
                )
            )
        return lines
