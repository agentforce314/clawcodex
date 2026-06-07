"""Shared primitives for transcript row widgets.

Every row is a plain Textual ``Widget`` so the :class:`TranscriptView`
container can scroll a homogeneous list. We deliberately avoid subclassing
``Static`` here because several rows (streaming text, tool activity) need
dynamic children, not a single rendered block.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static


class BaseRow(Widget):
    """Common base for transcript rows.

    Subclasses typically override :meth:`compose` and add their own
    reactive attributes. ``BaseRow`` only provides the vertical padding
    / margin that keeps consecutive rows visually separated.
    """

    DEFAULT_CSS = """
    BaseRow {
        layout: vertical;
        height: auto;
        margin: 0 0 1 0;
    }
    """


class SystemMessage(BaseRow):
    """A muted, one-off informational / error row.

    Analogue of ``<Text dimColor>…</Text>`` usages scattered through
    ``typescript/src/screens/REPL.tsx`` (onboarding callouts, exit
    reminders, and error rows emitted by :meth:`AgentBridge`).
    """

    DEFAULT_CSS = """
    SystemMessage {
        height: auto;
        margin: 0 0 1 0;
    }
    SystemMessage.-error > Static {
        color: $error;
    }
    SystemMessage.-muted > Static {
        color: $text-muted;
    }
    """

    def __init__(self, text: str, *, style: str = "muted") -> None:
        super().__init__()
        self._text = text
        self._style = style
        if style == "error":
            self.add_class("-error")
        else:
            self.add_class("-muted")

    def compose(self) -> ComposeResult:
        yield Static(Text(self._text), markup=False)

    def update_text(self, text: str) -> None:
        """Swap the row contents in place without re-mounting children."""
        self._text = text
        try:
            static = self.query_one(Static)
            static.update(Text(text))
        except Exception:
            # Before mount — next compose() will pick up the new text.
            pass

    def snapshot(self) -> Text:
        """Return a Rich :class:`Text` for post-exit scrollback dump."""

        style = "dim" if self._style != "error" else "red"
        return Text(self._text, style=style)


class RowHeader(Static):
    """One-line row header shared by user / assistant / tool rows.

    Kept as a standalone ``Static`` so row subclasses can reuse the same
    styling across the transcript without duplicating CSS.
    """

    DEFAULT_CSS = """
    RowHeader {
        height: 1;
        width: auto;
        padding: 0 1;
    }
    RowHeader.-user { color: $primary; text-style: bold; }
    RowHeader.-assistant { color: $secondary; text-style: bold; }
    RowHeader.-tool { color: $warning; text-style: bold; }
    RowHeader.-tool-success { color: $success; text-style: bold; }
    RowHeader.-tool-error { color: $error; text-style: bold; }
    """


def row_container() -> Vertical:
    """Shortcut for the standard ``Vertical`` that wraps a row body.

    Extracted so future rows stay consistent without copy-pasting the
    DEFAULT_CSS sizing rules.
    """

    return Vertical()
