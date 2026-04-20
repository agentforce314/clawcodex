"""Model picker dialog.

Port of ``components/ModelPicker.tsx``. The dialog lists the available
models for the active provider, shows the current selection, and
resolves with the chosen model id on Enter. Phase 2 skips the inline
"effort" left/right cycle — that lives in :class:`EffortPickerScreen`
and is reachable as a standalone step via the ``/effort`` command.
"""

from __future__ import annotations

from typing import Callable, Iterator, Sequence

from textual.widget import Widget

from ..widgets.select_list import SelectList, SelectOption
from .dialog_base import DialogScreen


class ModelPickerScreen(DialogScreen[str | None]):
    """Modal picker that resolves with the selected model id.

    The result is ``None`` when the user cancels (Esc) so callers can
    distinguish "no change" from an explicit selection.
    """

    title_text = "Select model"
    subtitle_text = "Switch the model used for the rest of this session."
    footer_hint = "Enter to select · Esc to cancel"

    def __init__(
        self,
        *,
        models: Sequence[str],
        current_model: str | None = None,
        on_persist: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._models = list(models)
        self._current = current_model
        self._on_persist = on_persist
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        options: list[SelectOption] = []
        current_index = 0
        for idx, model in enumerate(self._models):
            desc = "current" if model == self._current else None
            options.append(SelectOption(label=model, value=model, description=desc))
            if model == self._current:
                current_index = idx
        self._select = SelectList(
            options or [SelectOption(label="(no models available)", disabled=True)],
            initial_index=current_index,
            allow_cancel=True,
        )
        yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    # ---- select events ----
    def on_select_list_option_selected(self, event: SelectList.OptionSelected) -> None:
        model = str(event.option.value)
        if self._on_persist is not None:
            try:
                self._on_persist(model)
            except Exception:
                pass
        self.dismiss(model)

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        self.dismiss(None)
