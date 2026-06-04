"""Generic free-text input modal for the interactive-command bridge.

Backs :meth:`src.tui.ui_host.TextualUIHost.prompt_text` so an interactive
command (``CommandType.INTERACTIVE``) gets a TUI text prompt without defining
its own screen. Mirrors :class:`src.tui.screens.generic_select.GenericSelectScreen`
— the same ``DialogScreen`` vocabulary — but yields a single ``Input`` widget
and the title/default/placeholder come from the command's
``ctx.ui.prompt_text(...)`` call rather than being hardcoded.

Resolves (via ``dismiss``) with the submitted string — which MAY be ``''`` (an
empty submit is valid input, mirroring TS ``TextInput.onSubmit('')``) — or
``None`` on cancel (Esc). That is the contract :class:`TextualUIHost` relays
back to the command body.
"""

from __future__ import annotations

from typing import Iterator, Optional

from textual.widget import Widget
from textual.widgets import Input


from .dialog_base import DialogScreen


class GenericInputScreen(DialogScreen[Optional[str]]):
    """Modal free-text prompt.

    Resolves with the submitted value (Enter — empty string IS a valid
    submit) or ``None`` (Esc / cancel).
    """

    footer_hint = "Enter to submit · Esc to cancel"

    def __init__(
        self,
        *,
        title: str,
        default: str = "",
        placeholder: Optional[str] = None,
    ) -> None:
        super().__init__()
        # title_text drives DialogScreen.compose(); set after super().__init__
        # (compose runs at mount, so the assignment is in time).
        self.title_text = title
        self._default = default
        self._placeholder = placeholder
        self._input: Input | None = None

    def build_body(self) -> Iterator[Widget]:
        self._input = Input(
            value=self._default,
            placeholder=self._placeholder or "",
        )
        yield self._input

    def _post_mount(self) -> None:
        if self._input is not None:
            self._input.focus()

    # ---- events ----
    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Empty string is a valid submit (empty != cancel); only Esc cancels,
        # via DialogScreen.action_cancel -> dismiss(None).
        self.dismiss(event.value)


__all__ = ["GenericInputScreen"]
