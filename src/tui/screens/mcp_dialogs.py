"""MCP connection and elicitation dialogs.

Ports the ink reference's MCP surface at the level of fidelity the
Textual UI actually needs:

* :class:`McpListScreen` — list of configured MCP servers with their
  live status; the user can pick one to enable/disable (we return the
  selected server id on dismiss).
* :class:`McpToolListScreen` — per-server tool browser that resolves
  with the chosen tool name.
* :class:`McpElicitationScreen` — the prompt-style dialog MCP tools
  raise to ask the user for a single free-form value. Only the
  "text" (single-line) and "select" variants are supported in Phase
  3; URL / form variants stay in the ink reference for a later
  session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterator, Literal, Sequence

from rich.text import Text
from textual.widget import Widget
from textual.widgets import Input, Static

from ..widgets.select_list import SelectList, SelectOption
from .dialog_base import DialogScreen


@dataclass
class McpServer:
    id: str
    name: str
    status: Literal["connected", "disconnected", "error"] = "disconnected"
    tools: list[str] = field(default_factory=list)
    error: str | None = None


class McpListScreen(DialogScreen[str | None]):
    """Modal list of configured MCP servers."""

    title_text = "MCP servers"
    footer_hint = "Enter to open · Esc to close"

    def __init__(
        self,
        *,
        servers: Sequence[McpServer],
        on_pick: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self._servers = list(servers)
        self._on_pick = on_pick

    def build_body(self) -> Iterator[Widget]:
        if not self._servers:
            yield Static(
                Text("No MCP servers configured.", style="dim"),
                markup=False,
            )
            return
        options = [
            SelectOption(
                label=s.name,
                value=s.id,
                description=_mcp_status_summary(s),
                disabled=s.status == "error",
            )
            for s in self._servers
        ]
        self._select = SelectList(options, allow_cancel=True)
        yield self._select

    def _post_mount(self) -> None:
        if self._servers:
            self._select.focus()

    def on_select_list_option_selected(self, event: SelectList.OptionSelected) -> None:
        server_id = str(event.option.value)
        if self._on_pick is not None:
            try:
                self._on_pick(server_id)
            except Exception:
                pass
        self.dismiss(server_id)

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        self.dismiss(None)


def _mcp_status_summary(server: McpServer) -> str:
    tool_suffix = f" ({len(server.tools)} tools)" if server.tools else ""
    if server.status == "error":
        err = f": {server.error}" if server.error else ""
        return f"error{err}"
    if server.status == "connected":
        return f"connected{tool_suffix}"
    return "disconnected"


class McpToolListScreen(DialogScreen[str | None]):
    """Modal list of tools provided by a single MCP server."""

    title_text = "MCP tools"
    footer_hint = "Enter to pick · Esc to close"

    def __init__(
        self,
        *,
        server_name: str,
        tools: Sequence[str],
    ) -> None:
        super().__init__()
        self._server_name = server_name
        self._tools = list(tools)
        self.subtitle_text = f"From MCP server: {server_name}"

    def build_body(self) -> Iterator[Widget]:
        if not self._tools:
            yield Static(
                Text("No tools available.", style="dim"), markup=False
            )
            return
        options = [SelectOption(label=t, value=t) for t in self._tools]
        self._select = SelectList(options, allow_cancel=True)
        yield self._select

    def _post_mount(self) -> None:
        if self._tools:
            self._select.focus()

    def on_select_list_option_selected(self, event: SelectList.OptionSelected) -> None:
        self.dismiss(str(event.option.value))

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        self.dismiss(None)


class McpElicitationScreen(DialogScreen[str | None]):
    """Modal prompt for MCP-triggered elicitation requests.

    Two variants are supported:
      * ``variant="text"`` — free-form input; Enter submits the value.
      * ``variant="select"`` — choose from ``options``.

    Both resolve with the chosen string value or ``None`` on Esc.
    """

    title_text = "MCP request"
    footer_hint = "Enter to submit · Esc to cancel"

    def __init__(
        self,
        *,
        prompt: str,
        variant: Literal["text", "select"] = "text",
        options: Sequence[str] | None = None,
        default: str = "",
    ) -> None:
        super().__init__()
        self.subtitle_text = prompt
        self._variant = variant
        self._options = list(options or [])
        self._default = default
        self._input: Input | None = None
        self._select: SelectList | None = None

    def build_body(self) -> Iterator[Widget]:
        if self._variant == "select" and self._options:
            self._select = SelectList(
                [SelectOption(label=o, value=o) for o in self._options],
                allow_cancel=True,
            )
            yield self._select
        else:
            self._input = Input(value=self._default, placeholder="type response…")
            yield self._input

    def _post_mount(self) -> None:
        if self._input is not None:
            self._input.focus()
        elif self._select is not None:
            self._select.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_select_list_option_selected(self, event: SelectList.OptionSelected) -> None:
        self.dismiss(str(event.option.value))

    def on_select_list_selection_cancelled(
        self, _: SelectList.SelectionCancelled
    ) -> None:
        self.dismiss(None)


__all__ = [
    "McpElicitationScreen",
    "McpListScreen",
    "McpServer",
    "McpToolListScreen",
]
