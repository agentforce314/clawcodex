"""Modal overlay listing every active keybinding, grouped by category.

Mirrors ``typescript/src/components/PromptInput/PromptInputHelpMenu.tsx``.
Triggered via ``?`` keystroke or the ``/help`` slash command (the slash
command path is wired in :mod:`src.tui.commands`; the keystroke trigger
is dispatched through the Phase-2 :class:`KeybindingDispatcher` action
``promptinput.help`` once a host screen registers a handler that
launches this modal).

The widget reads the binding set from a :class:`KeybindingDispatcher` so
user overrides flow through naturally — there is no hand-rolled "default
list" that would drift from the resolver's authoritative state.
"""

from __future__ import annotations

from collections.abc import Iterable

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..keybindings_dispatcher import KeybindingDispatcher
from ..keybindings_schema import KeybindingEntry


class PromptInputHelpMenu(ModalScreen[None]):
    """Modal listing the active keybindings in a help-style table."""

    DEFAULT_CSS = """
    PromptInputHelpMenu {
        align: center middle;
    }
    PromptInputHelpMenu > Vertical {
        width: 60%;
        max-width: 80;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    PromptInputHelpMenu Static.-title {
        color: $primary;
        text-style: bold;
        padding: 0 0 1 0;
    }
    PromptInputHelpMenu Static.-row {
        padding: 0 0 0 0;
    }
    PromptInputHelpMenu Static.-hint {
        color: $text-muted;
        padding: 1 0 0 0;
    }
    """

    BINDINGS = [
        ("escape", "dismiss_modal", "Close"),
        ("q", "dismiss_modal", "Close"),
    ]

    def __init__(self, dispatcher: KeybindingDispatcher) -> None:
        super().__init__()
        self._dispatcher = dispatcher

    def compose(self) -> ComposeResult:
        body = Vertical()
        yield body

    def on_mount(self) -> None:
        body = self.query_one(Vertical)
        body.mount(Static(Text("Keybindings", style="bold"), classes="-title"))
        bindings = self._dispatcher.tracker.bindings
        # ``ChordTracker.bindings`` returns ``ChordBinding`` records;
        # convert them to a uniform shape for rendering.
        rows = list(_render_rows(bindings))
        if not rows:
            body.mount(Static(Text("(no bindings registered)", style="dim")))
        for row in rows:
            body.mount(Static(row, classes="-row", markup=False))
        body.mount(
            Static(
                Text("Press Esc or q to close.", style="dim"),
                classes="-hint",
                markup=False,
            )
        )

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


def _render_rows(bindings: Iterable) -> Iterable[Text]:
    """Yield one styled row per binding, sorted by action.

    Accepts the legacy ``ChordBinding`` shape (used by ``ChordTracker``)
    or the typed :class:`KeybindingEntry` shape — both share the
    ``keys: tuple[str, ...]`` + ``action`` + ``description`` fields the
    renderer needs.
    """

    items: list[tuple[str, tuple[str, ...], str]] = []
    for b in bindings:
        keys = getattr(b, "keys", ())
        action = getattr(b, "action", "")
        description = getattr(b, "description", "") or action
        if keys and action:
            items.append((action, tuple(keys), description))
    items.sort(key=lambda r: r[0])
    for action, keys, description in items:
        chord = " ".join(keys)
        text = Text()
        text.append(f"{chord:<14}", style="bold")
        text.append("  ")
        text.append(description, style="default")
        text.append("  ")
        text.append(f"({action})", style="dim")
        yield text


def _entries_to_legacy(entries: Iterable[KeybindingEntry]) -> Iterable:
    """Adapter — keep ``KeybindingEntry`` callers working uniformly."""

    return entries  # KeybindingEntry already exposes the shape we need.


__all__ = ["PromptInputHelpMenu"]
