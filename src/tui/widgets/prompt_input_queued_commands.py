"""Chip row showing slash commands queued behind the active turn.

Mirrors ``typescript/src/components/PromptInput/PromptInputQueuedCommands.tsx``.
When the user submits a slash command while the agent is busy with a
prior turn, that command is queued; the chip row above the prompt
input keeps the queue visible until the agent drains it.

The widget is content-driven — callers feed it a tuple of pending
command names via :meth:`set_queue` and the widget auto-hides when the
queue is empty.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class PromptInputQueuedCommands(Widget):
    """One-line chip row that hides when the queue is empty."""

    DEFAULT_CSS = """
    PromptInputQueuedCommands {
        height: auto;
        max-height: 1;
        padding: 0 1;
    }
    PromptInputQueuedCommands.-hidden {
        display: none;
    }
    """

    queue: reactive[tuple[str, ...]] = reactive(
        (), always_update=True, layout=True
    )

    def compose(self) -> ComposeResult:
        yield Static(Text(""), classes="-row", markup=False)

    def on_mount(self) -> None:
        self._refresh()

    def watch_queue(
        self, _old: tuple[str, ...], _new: tuple[str, ...]
    ) -> None:
        self._refresh()

    def set_queue(self, queue: tuple[str, ...] | list[str]) -> None:
        """Replace the queue. Empty tuple hides the row."""

        self.queue = tuple(item for item in queue if item)

    # ---- internals ----
    def _refresh(self) -> None:
        row = self._row()
        if row is None:
            return
        if not self.queue:
            self.add_class("-hidden")
            row.update(Text(""))
            return
        self.remove_class("-hidden")
        rendered = Text("queued: ", style="dim")
        for i, name in enumerate(self.queue):
            if i:
                rendered.append("  ", style="dim")
            # Display each command as a styled chip — matches the chapter
            # UX of "this hasn't fired yet but it's lined up".
            rendered.append(f"[ {name} ]", style="cyan")
        row.update(rendered)

    def _row(self) -> Static | None:
        try:
            for static in self.query(Static):
                if static.has_class("-row"):
                    return static
        except Exception:
            return None
        return None


__all__ = ["PromptInputQueuedCommands"]
