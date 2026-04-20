"""DiffDialog — modal viewer for pending/staged diffs.

Port of ``components/diff/DiffDialog.tsx``. The ink reference lets
the user cycle through per-turn file deltas (``[``, ``]``) and drill
into a single file (``Enter``) to see its structured diff. Phase 3
delivers a lighter version: a list of file diffs on the left, the
structured diff body below, and arrow-key navigation between files.

Callers pass a list of :class:`FileDiff` records; the dialog is
read-only (``Esc`` dismisses, no patch-apply side effect).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from ..widgets.select_list import SelectList, SelectOption
from ..widgets.structured_diff import (
    StructuredDiff,
    count_changes,
    parse_unified_diff,
)
from .dialog_base import DialogScreen


@dataclass
class FileDiff:
    """A single file's patch within a larger diff payload."""

    path: str
    patch: str

    @property
    def summary(self) -> str:
        add, remove = count_changes(parse_unified_diff(self.patch))
        return f"+{add} -{remove}"


class DiffDialogScreen(DialogScreen[str | None]):
    """Modal diff viewer.

    Resolves with the path of the selected file on Enter, or ``None``
    on Esc. Callers that want to act on the selection (e.g. open the
    file in an editor) can use the dismissal value; Phase 3 itself
    does not wire any side-effect onto selection.
    """

    title_text = "Diff"
    footer_hint = "↑/↓ switch file · Enter choose · Esc close"

    DEFAULT_CSS = """
    DiffDialogScreen > #dialog-panel {
        width: 100;
        max-width: 95%;
        height: 80%;
    }
    DiffDialogScreen #diff-body {
        height: 1fr;
        margin-top: 1;
        border: round $primary-darken-2;
        padding: 0 1;
    }
    DiffDialogScreen #diff-summary {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def __init__(self, *, files: Sequence[FileDiff]) -> None:
        super().__init__()
        self._files = list(files)
        self._active: int = 0

    def build_body(self) -> Iterator[Widget]:
        if not self._files:
            yield Static(
                Text("No changes to display.", style="dim"), markup=False
            )
            return
        options = [
            SelectOption(
                label=f.path,
                value=idx,
                description=f.summary,
            )
            for idx, f in enumerate(self._files)
        ]
        self._select = SelectList(options, initial_index=0, allow_cancel=True)
        yield self._select
        self._summary = Static(Text(""), id="diff-summary", markup=False)
        yield self._summary
        self._body = StructuredDiff(patch=self._files[0].patch)
        self._body.id = "diff-body"
        yield self._body

    def _post_mount(self) -> None:
        if self._files:
            self._select.focus()
            self._refresh_summary(0)

    # ---- select events ----
    def on_select_list_option_highlighted(
        self, event: SelectList.OptionHighlighted
    ) -> None:
        idx = int(event.option.value)
        if idx == self._active or idx < 0 or idx >= len(self._files):
            return
        self._active = idx
        # Swap the structured diff body in place without remounting
        # the summary. StructuredDiff exposes ``set_patch`` for this.
        self._body.set_patch(self._files[idx].patch)
        self._refresh_summary(idx)

    def on_select_list_option_selected(self, event: SelectList.OptionSelected) -> None:
        idx = int(event.option.value)
        if idx < 0 or idx >= len(self._files):
            self.dismiss(None)
            return
        self.dismiss(self._files[idx].path)

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        self.dismiss(None)

    def _refresh_summary(self, idx: int) -> None:
        if not self._files:
            return
        f = self._files[idx]
        add, remove = count_changes(parse_unified_diff(f.patch))
        self._summary.update(
            Text(
                f"{f.path}   +{add} / -{remove}",
                style="dim",
            )
        )


__all__ = ["DiffDialogScreen", "FileDiff"]
