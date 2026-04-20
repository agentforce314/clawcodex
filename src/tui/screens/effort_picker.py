"""Reasoning-effort picker dialog.

Port of ``components/EffortPicker.tsx``. Exposes the same four levels
the TS client offers (``auto``, ``low``, ``medium``, ``high``) and
resolves with the selected value (``None`` means "auto"; Esc cancels
and also resolves with ``None`` but with ``persist=False`` — the caller
distinguishes via the second tuple element).
"""

from __future__ import annotations

from typing import Callable, Iterator

from textual.widget import Widget

from ..widgets.select_list import SelectList, SelectOption
from .dialog_base import DialogScreen


EffortValue = str  # one of: "auto", "low", "medium", "high"


class EffortPickerScreen(DialogScreen[tuple[EffortValue | None, bool]]):
    """Modal picker for reasoning effort.

    Resolves with ``(effort, persisted)`` where ``persisted`` is
    ``True`` when the user confirmed with Enter (and the caller should
    write the choice to config) and ``False`` when the user cancelled.
    """

    title_text = "Set reasoning effort"
    subtitle_text = "Higher effort trades latency for quality."
    footer_hint = "Enter to select · Esc to cancel"

    EFFORT_LEVELS: list[tuple[str, str, str | None]] = [
        ("Auto", "auto", "let the provider decide"),
        ("Low", "low", "fastest, shallowest"),
        ("Medium", "medium", "balanced"),
        ("High", "high", "slowest, deepest"),
    ]

    def __init__(
        self,
        *,
        current: str | None = None,
        on_persist: Callable[[str | None], None] | None = None,
    ) -> None:
        super().__init__()
        self._current = (current or "auto").lower()
        self._on_persist = on_persist
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        options: list[SelectOption] = []
        current_index = 0
        for idx, (label, value, description) in enumerate(self.EFFORT_LEVELS):
            options.append(
                SelectOption(
                    label=label,
                    value=value,
                    description=description,
                )
            )
            if value == self._current:
                current_index = idx
        self._select = SelectList(
            options,
            initial_index=current_index,
            allow_cancel=True,
        )
        yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    # ---- events ----
    def on_select_list_option_selected(self, event: SelectList.OptionSelected) -> None:
        value = str(event.option.value)
        effort: str | None = None if value == "auto" else value
        if self._on_persist is not None:
            try:
                self._on_persist(effort)
            except Exception:
                pass
        self.dismiss((effort, True))

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        self.dismiss((None, False))
