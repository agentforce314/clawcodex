"""Generic single-select modal for the interactive-command bridge.

Backs :meth:`src.tui.ui_host.TextualUIHost.select` so an interactive command
(``CommandType.INTERACTIVE``) gets a TUI picker without defining its own
screen. Mirrors :class:`src.tui.screens.effort_picker.EffortPickerScreen` —
the same ``DialogScreen`` + ``SelectList`` vocabulary — but the title and rows
are passed in from the command's ``ctx.ui.select(...)`` call rather than
hardcoded.

Resolves (via ``dismiss``) with the chosen ``UIOption.value`` or ``None`` on
cancel — the contract :class:`TextualUIHost` relays back to the command body.
"""

from __future__ import annotations

from typing import Iterator, Optional, Sequence

from textual.widget import Widget

from src.command_system.types import UIOption

from ..widgets.select_list import SelectList, SelectOption
from .dialog_base import DialogScreen


class GenericSelectScreen(DialogScreen[Optional[str]]):
    """Modal picker over a list of :class:`UIOption`.

    Resolves with the selected ``value`` (Enter) or ``None`` (Esc / cancel).
    """

    footer_hint = "Enter to select · Esc to cancel"

    def __init__(
        self,
        *,
        title: str,
        options: Sequence[UIOption],
        current: Optional[str] = None,
    ) -> None:
        super().__init__()
        # title_text drives DialogScreen.compose(); set after super().__init__
        # (compose runs at mount, so the assignment is in time).
        self.title_text = title
        self._options = list(options)
        self._current = current
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        rows: list[SelectOption] = []
        current_index = 0
        for idx, opt in enumerate(self._options):
            rows.append(
                SelectOption(
                    label=opt.label,
                    value=opt.value,
                    description=opt.description,
                )
            )
            if self._current is not None and opt.value == self._current:
                current_index = idx
        self._select = SelectList(
            rows,
            initial_index=current_index,
            allow_cancel=True,
        )
        yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    # ---- events ----
    def on_select_list_option_selected(
        self, event: SelectList.OptionSelected
    ) -> None:
        self.dismiss(str(event.option.value))

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        self.dismiss(None)


__all__ = ["GenericSelectScreen"]
