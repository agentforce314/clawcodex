"""User turn row.

Port of ``typescript/src/components/messages/UserTextMessage.tsx``.
Renders a single row with a ``❯`` prefix in the primary color followed
by the user's prompt in bold text. Multi-line prompts are preserved.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from .base import BaseRow


class UserTextMessage(BaseRow):
    """A user prompt shown in the transcript."""

    DEFAULT_CSS = """
    UserTextMessage {
        height: auto;
    }
    UserTextMessage > Static {
        padding: 0 1;
    }
    """

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def compose(self) -> ComposeResult:
        yield Static(self._build_text(), markup=False)

    def _build_text(self) -> Text:
        prefix = Text("❯ ", style="bold #8ab4f8")
        body = Text(self._text, style="bold")
        return prefix + body

    def snapshot(self) -> Text:
        """Return a Rich :class:`Text` for post-exit scrollback dump."""

        return self._build_text()
