"""``#`` memory-note target picker (components C9).

Reuses the ``/memory`` hierarchy options (TS ``MemoryFileSelector`` —
User memory / Project memory / enumerated files). Dismisses with the
chosen file path, or ``None`` on Esc (= cancel; the note returns to the
prompt so nothing is lost). Title phrasing is port-chosen — the
vendored TS snapshot has no ``#`` dialog to quote.
"""

from __future__ import annotations

from typing import Iterator, Sequence

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from src.command_system.types import UIOption
from src.tui.widgets.select_list import SelectList, SelectOption

from .dialog_base import DialogScreen


class MemorySaveScreen(DialogScreen[str | None]):
    """Pick which memory file a ``#`` note should be appended to."""

    title_text = "Save memory to:"
    footer_hint = "Enter selects · Esc cancels"
    border_variant = "primary"

    def __init__(self, note: str, options: Sequence[UIOption]) -> None:
        super().__init__()
        self._note = note
        self._options = list(options)
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        preview = self._note if len(self._note) <= 120 else (
            self._note[:117] + "..."
        )
        yield Static(Text(f"# {preview}", style="bold"), markup=False)
        self._select = SelectList(
            [
                SelectOption(
                    label=str(opt.label),
                    value=str(opt.value),
                    description=getattr(opt, "description", None),
                )
                for opt in self._options
            ],
            allow_cancel=True,
        )
        yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    def on_select_list_option_selected(
        self, event: SelectList.OptionSelected
    ) -> None:
        self.dismiss(str(event.option.value))

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        self.dismiss(None)


__all__ = ["MemorySaveScreen"]
