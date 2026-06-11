"""Project ``.mcp.json`` server approval dialog (components C7).

Port of TS ``MCPServerApprovalDialog.tsx`` (single pending server; the
multi-server case iterates this same dialog rather than porting the
separate multiselect — degraded scope, documented). Dismisses with the
chosen action (``enable`` / ``enable_all`` / ``disable``) or ``None``
on Esc (= remain pending; asked again next launch, exactly TS's
behavior for an unanswered dialog).
"""

from __future__ import annotations

from typing import Iterator

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Static

from src.tui.widgets.select_list import SelectList, SelectOption

from .dialog_base import DialogScreen


class McpApprovalScreen(DialogScreen[str | None]):
    """One pending ``.mcp.json`` server → enable / enable-all / disable."""

    title_text = "New MCP server found in .mcp.json"
    footer_hint = "Enter selects · Esc decides later"
    border_variant = "warning"

    def __init__(self, server_name: str) -> None:
        super().__init__()
        self._server_name = server_name
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        yield Static(
            Text(
                f"This project's .mcp.json configures the server "
                f"'{self._server_name}'. MCP servers run as local "
                "processes — only enable servers you trust.",
                style="dim",
            ),
            markup=False,
        )
        self._select = SelectList(
            [
                SelectOption(
                    label=f"Yes, enable '{self._server_name}'",
                    value="enable",
                ),
                SelectOption(
                    label="Yes, and enable ALL servers this project's "
                    ".mcp.json configures (now and in the future)",
                    value="enable_all",
                ),
                SelectOption(
                    label=f"No, disable '{self._server_name}'",
                    value="disable",
                ),
            ],
            allow_cancel=True,
        )
        yield self._select

    def _post_mount(self) -> None:
        if self._select is not None:
            self._select.focus()

    def on_select_list_option_selected(
        self, event: SelectList.OptionSelected
    ) -> None:
        self.dismiss(str(event.option.value))

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        # Esc = decide later: stays pending, asked again next launch.
        self.dismiss(None)


__all__ = ["McpApprovalScreen"]
