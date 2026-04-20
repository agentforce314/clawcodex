"""Prompt history fuzzy search dialog.

Port of ``components/HistorySearchDialog.tsx``. Shows the user's
previous prompts with a live-updating fuzzy filter, resolving with the
chosen prompt text on Enter (or ``None`` on Esc).

The ranking logic mirrors the TS ``fuzzyMatch`` helper: exact
case-insensitive substring first, then subsequence match with a
penalty proportional to the gap between matched characters. That way
typing ``gst`` surfaces ``git status`` before ``greatest``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator, Sequence

from rich.text import Text
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Static

from ..widgets.select_list import SelectList, SelectOption
from .dialog_base import DialogScreen


@dataclass
class HistoryEntry:
    prompt: str
    timestamp: float = 0.0
    meta: dict = field(default_factory=dict)


class HistorySearchScreen(DialogScreen[str | None]):
    """Modal screen that lets the user re-submit a previous prompt."""

    title_text = "Search prompt history"
    footer_hint = "Enter to select · Esc to cancel"

    # Allow arrow keys to fall through to the SelectList even when the
    # Input has focus — we wire this manually in ``on_key``.
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("up", "move(-1)", "Previous", show=False),
        Binding("down", "move(1)", "Next", show=False),
        Binding("ctrl+p", "move(-1)", "Previous", show=False),
        Binding("ctrl+n", "move(1)", "Next", show=False),
        Binding("enter", "select_current", "Select", show=False),
    ]

    filter_text: reactive[str] = reactive("")

    def __init__(
        self,
        *,
        entries: Sequence[HistoryEntry] | Sequence[str],
        initial_query: str = "",
        on_select: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._entries = [
            e if isinstance(e, HistoryEntry) else HistoryEntry(prompt=str(e))
            for e in entries
        ]
        self._initial_query = initial_query
        self._on_select = on_select
        self._input: Input | None = None
        self._list: SelectList | None = None
        self._count_label: Static | None = None

    def build_body(self) -> Iterator[Widget]:
        self._input = Input(placeholder="type to filter…", value=self._initial_query)
        yield self._input
        self._count_label = Static(Text(""), markup=False)
        yield self._count_label
        self._list = SelectList(self._options_for_query(self._initial_query))
        yield self._list

    def _post_mount(self) -> None:
        if self._input is not None:
            self._input.focus()
        self.filter_text = self._initial_query
        self._update_count_label()

    # ---- input handling ----
    def on_input_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value

    def on_input_submitted(self, _: Input.Submitted) -> None:
        # Input captures Enter; route it to the same action a keyboard
        # binding would so the dialog resolves consistently whether the
        # user presses Enter inside the Input or inside the SelectList.
        self.action_select_current()

    def watch_filter_text(self, value: str) -> None:
        if self._list is None:
            return
        self._list.set_options(self._options_for_query(value))
        self._update_count_label()

    def _update_count_label(self) -> None:
        if self._count_label is None or self._list is None:
            return
        total = len(self._entries)
        shown = len(self._list.options)
        self._count_label.update(
            Text(f"  {shown} / {total} match{'es' if shown != 1 else ''}", style="dim")
        )

    # ---- navigation while Input has focus ----
    def action_move(self, delta: int) -> None:
        if self._list is not None:
            self._list.action_move(delta)

    def action_select_current(self) -> None:
        if self._list is None or self._list.current is None:
            # Nothing to select; fall through to treating Enter as
            # "submit whatever is typed" so the user can run novel text.
            typed = self._input.value.strip() if self._input else ""
            if typed:
                self._resolve(typed)
            else:
                self.dismiss(None)
            return
        option = self._list.current
        self._resolve(str(option.value))

    # ---- helpers ----
    def _options_for_query(self, query: str) -> list[SelectOption]:
        scored = _rank_entries(self._entries, query)
        out: list[SelectOption] = []
        for entry, _ in scored[:50]:
            label = entry.prompt.splitlines()[0] if entry.prompt else ""
            if len(label) > 90:
                label = label[:87] + "…"
            out.append(SelectOption(label=label or "(empty)", value=entry.prompt))
        return out

    def _resolve(self, prompt_text: str) -> None:
        if self._on_select is not None:
            try:
                self._on_select(prompt_text)
            except Exception:
                pass
        self.dismiss(prompt_text)

    def action_cancel(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------
# Ranking helpers — exposed at module level so tests can exercise them
# directly without spinning up Textual.
# --------------------------------------------------------------------


def fuzzy_score(text: str, query: str) -> tuple[bool, int]:
    """Return ``(matched, score)``; higher score = better match.

    Matching rules:
      * Empty query matches everything with score 0.
      * Case-insensitive substring match scores ``1000 - position``.
      * Subsequence match scores ``500 - gap_penalty``.
      * Anything else returns ``(False, 0)``.
    """

    if not query:
        return True, 0
    text_lower = text.lower()
    q_lower = query.lower()
    pos = text_lower.find(q_lower)
    if pos >= 0:
        return True, 1000 - pos
    # Subsequence scan.
    ti = 0
    last_match = -1
    gap = 0
    for qc in q_lower:
        while ti < len(text_lower) and text_lower[ti] != qc:
            ti += 1
        if ti >= len(text_lower):
            return False, 0
        if last_match >= 0:
            gap += ti - last_match - 1
        last_match = ti
        ti += 1
    return True, max(0, 500 - gap)


def _rank_entries(
    entries: Sequence[HistoryEntry], query: str
) -> list[tuple[HistoryEntry, int]]:
    scored: list[tuple[HistoryEntry, int]] = []
    for entry in entries:
        matched, score = fuzzy_score(entry.prompt, query)
        if matched:
            scored.append((entry, score))
    # Higher score first; stable-sort preserves insertion order for ties
    # (so most recent entries win when scores are equal).
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored
