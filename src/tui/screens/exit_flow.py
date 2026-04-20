"""Exit confirmation dialog.

Port of ``components/ExitFlow.tsx`` + ``components/WorktreeExitDialog.tsx``.
Phase 2 models the user-facing behaviour (confirm before leaving, with
an option to discard in-flight conversation) but intentionally skips
the git-worktree aware branch — worktree cleanup belongs in Phase 3
alongside the diff/MCP work that already reads git state.
"""

from __future__ import annotations

from typing import Callable, Iterator, Literal

from textual.widget import Widget

from ..widgets.select_list import SelectList, SelectOption
from .dialog_base import DialogScreen


ExitAction = Literal["quit", "quit-clear", "cancel"]


class ExitFlowScreen(DialogScreen["ExitAction"]):
    """Confirm-exit dialog pushed when the user hits Ctrl+D / ``/exit``.

    Resolves with:
      * ``"quit"``       — leave, keep conversation in session history.
      * ``"quit-clear"`` — leave and clear the current conversation.
      * ``"cancel"``     — stay (Esc).
    """

    title_text = "Leave Claw Codex?"
    footer_hint = "Enter to choose · Esc to stay"
    border_variant = "warning"

    def __init__(
        self,
        *,
        has_inflight_work: bool = False,
        on_choice: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._inflight = has_inflight_work
        self._on_choice = on_choice
        self.subtitle_text = (
            "Work is still running — quitting will abort it."
            if has_inflight_work
            else "Your conversation will be saved to the session history."
        )

    def build_body(self) -> Iterator[Widget]:
        self._select = SelectList(
            [
                SelectOption(
                    label="Quit",
                    value="quit",
                    description="save and exit",
                ),
                SelectOption(
                    label="Quit & clear conversation",
                    value="quit-clear",
                    description="discard history then exit",
                ),
                SelectOption(
                    label="Stay",
                    value="cancel",
                    description="cancel exit",
                ),
            ],
            allow_cancel=True,
        )
        yield self._select

    def _post_mount(self) -> None:
        self._select.focus()

    def on_select_list_option_selected(self, event: SelectList.OptionSelected) -> None:
        value = str(event.option.value)
        if self._on_choice is not None:
            try:
                self._on_choice(value)
            except Exception:
                pass
        self.dismiss(value)  # type: ignore[arg-type]

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        if self._on_choice is not None:
            try:
                self._on_choice("cancel")
            except Exception:
                pass
        self.dismiss("cancel")
