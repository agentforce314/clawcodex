"""Permission request modal screen.

Phase 1 parity for ``typescript/src/components/permissions/PermissionRequest.tsx``.
The ink reference mounts a full ``PermissionRequest`` overlay inside
``FullscreenLayout.overlay`` that *steals input* until the user
approves / denies the pending tool call. In Textual we model that as a
``ModalScreen`` pushed onto the screen stack: Textual handles the
keyboard priority (modals are always on top of the pushed stack).

Tool-specific bodies (``BashPermissionRequest``, ``EditPermissionRequest``, …)
land in Phase 2 — Phase 1 renders a unified preview that works for every
tool.
"""

from __future__ import annotations

from typing import Any

from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Middle, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..messages import PermissionResolved
from ..state import PendingPermission


class PermissionModal(ModalScreen[bool]):
    """Modal that blocks input until the user decides on a tool call."""

    BINDINGS = [
        Binding("y", "allow", "Allow", show=False),
        Binding("n", "deny", "Deny", show=False),
        Binding("escape", "deny", "Deny", show=False),
    ]

    DEFAULT_CSS = """
    PermissionModal {
        align: center middle;
    }
    PermissionModal > Middle > Center > #panel {
        width: 72;
        max-width: 90%;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    PermissionModal #title {
        color: $warning;
        text-style: bold;
        margin-bottom: 1;
    }
    PermissionModal #buttons {
        height: auto;
        margin-top: 1;
    }
    PermissionModal Button {
        min-width: 10;
        margin-right: 2;
    }
    PermissionModal Button.-allow {
        background: $success;
        color: $background;
    }
    PermissionModal Button.-deny {
        background: $error;
        color: $background;
    }
    """

    def __init__(self, request: PendingPermission) -> None:
        super().__init__()
        self._request = request

    # ---- composition ----
    def compose(self) -> ComposeResult:
        panel = Vertical(id="panel")
        panel.border_title = "[ Permission required ]"
        with Middle():
            with Center():
                yield panel

    def on_mount(self) -> None:
        try:
            panel = self.query_one("#panel", Vertical)
        except Exception:
            return
        panel.mount(
            Static(
                Text(f"⚠  {self._request.tool_name}", style="bold"),
                id="title",
                markup=False,
            )
        )
        panel.mount(Static(Text(self._request.message), markup=False))
        input_preview = _preview_tool_input(self._request.tool_input)
        if input_preview is not None:
            panel.mount(Static(input_preview, markup=False))
        if self._request.suggestion:
            panel.mount(
                Static(
                    Text(self._request.suggestion, style="italic dim"),
                    markup=False,
                )
            )
        buttons = Vertical(id="buttons")
        panel.mount(buttons)
        buttons.mount(Button("Allow (y)", id="allow", classes="-allow"))
        buttons.mount(Button("Deny  (n)", id="deny", classes="-deny"))

    # ---- events ----
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "allow":
            self.action_allow()
        elif event.button.id == "deny":
            self.action_deny()

    def action_allow(self) -> None:
        self._resolve(True)

    def action_deny(self) -> None:
        self._resolve(False)

    # ---- internals ----
    def _resolve(self, allowed: bool) -> None:
        try:
            self._request.decide(allowed, False)
        except Exception:
            pass
        # Post the decision to the app so status-line / state observers
        # can react (e.g. drain the next queued permission).
        self.app.post_message(
            PermissionResolved(
                request_id=self._request.request_id,
                allowed=allowed,
                enable_setting=False,
            )
        )
        self.dismiss(allowed)


def _preview_tool_input(tool_input: Any) -> Panel | None:
    """Render the tool input as a compact preview panel."""

    if not tool_input:
        return None
    if isinstance(tool_input, dict):
        lines: list[str] = []
        for key, value in tool_input.items():
            if value is None:
                continue
            sv = str(value)
            if len(sv) > 200:
                sv = sv[:197] + "…"
            lines.append(f"{key}: {sv}")
            if len(lines) >= 6:
                break
        if not lines:
            return None
        body = "\n".join(lines)
    else:
        body = str(tool_input)
        if len(body) > 400:
            body = body[:397] + "…"
    return Panel(Text(body), border_style="bright_black", padding=(0, 1), title=escape("input"))
