"""Standalone tool result row.

Used for code paths where the agent loop emits a ``tool_result`` event
without a matching ``tool_use`` (e.g. replayed history). Normal in-turn
tool execution routes through :class:`AssistantToolUseMessage` instead.

Port of ``typescript/src/components/messages/AssistantToolResultMessage.tsx``
reduced to the fields the Python agent loop actually emits.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from .base import BaseRow, RowHeader


class ToolResultRow(BaseRow):
    DEFAULT_CSS = """
    ToolResultRow {
        height: auto;
    }
    ToolResultRow > Static.-body {
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        tool_name: str,
        summary: str,
        body: str | None = None,
        is_error: bool = False,
    ) -> None:
        super().__init__()
        self._tool_name = tool_name
        self._summary = summary
        self._body = body or ""
        self._is_error = is_error

    def compose(self) -> ComposeResult:
        glyph = "✗" if self._is_error else "✓"
        header = RowHeader(Text(f"{glyph} {self._summary or self._tool_name}"), markup=False)
        header.add_class("-tool-error" if self._is_error else "-tool-success")
        yield header
        if self._body.strip():
            yield Static(Text(self._body), markup=False, classes="-body")

    def snapshot(self) -> Text:
        """Return a Rich :class:`Text` for post-exit scrollback dump."""

        glyph = "✗" if self._is_error else "✓"
        style = "bold red" if self._is_error else "bold green"
        out = Text(f"{glyph} {self._summary or self._tool_name}", style=style)
        if self._body.strip():
            out.append("\n")
            out.append(self._body)
        return out
