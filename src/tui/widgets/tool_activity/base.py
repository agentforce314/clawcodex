"""Base class for tool-activity widgets.

Parity note: in ink, ``renderToolActivity`` dispatches per tool kind and
each renderer is responsible for its own *in-flight* view
(e.g. ``ShellProgress`` for bash) and *completion* view
(``GroupedToolUseContent`` summary). We keep the same structure — the
base widget exposes :meth:`on_result` so the owning row can swap body
contents without re-mounting the widget tree.
"""

from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class ToolActivity(Widget):
    """Baseline tool-activity widget.

    Subclasses typically override :meth:`inflight_text` and
    :meth:`result_body` to add tool-specific rendering; :meth:`compose`
    and :meth:`on_result` handle the lifecycle.
    """

    DEFAULT_CSS = """
    ToolActivity {
        layout: vertical;
        height: auto;
        padding: 0 1;
    }
    ToolActivity > Static.-inflight {
        color: $text-muted;
    }
    """

    def __init__(self, *, tool_name: str, tool_input: dict[str, Any]) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.tool_input = dict(tool_input or {})
        self._result_static: Static | None = None

    def compose(self) -> ComposeResult:
        yield Static(self.inflight_text(), markup=False, classes="-inflight")

    # ---- subclass hooks ----
    def inflight_text(self) -> Text:
        """Text shown while the tool is still running."""
        return Text("…", style="dim")

    def result_body(self, output: Any, *, is_error: bool) -> Any | None:
        """Return a Rich renderable for the completed body, or ``None``
        to leave the body empty (the row header already carries status).
        """
        return None

    # ---- lifecycle ----
    def on_result(
        self,
        output: Any,
        *,
        is_error: bool,
        error: str | None = None,
    ) -> None:
        try:
            for static in self.query(Static):
                if static.has_class("-inflight"):
                    static.remove()
                    break
        except Exception:
            pass
        body = self.result_body(output, is_error=is_error)
        if is_error and body is None and error:
            body = Panel(Text(error, style="red"), border_style="red", padding=(0, 1))
        if body is None:
            return
        result_widget = Static(body, markup=False)
        self._result_static = result_widget
        try:
            self.mount(result_widget)
        except Exception:
            pass


_BODY_MAX_CHARS = 1500
_BODY_MAX_LINES = 20


def truncated_panel(text: str, *, style: str = "green") -> Panel:
    """Render ``text`` in a bordered panel with stable truncation limits.

    Extracted from ``src.tui.widgets.transcript._truncated_panel`` so
    individual tool-activity widgets render output consistently.
    """

    s = (text or "").rstrip("\n")
    lines = s.split("\n")
    truncated = False
    if len(lines) > _BODY_MAX_LINES:
        lines = lines[:_BODY_MAX_LINES]
        truncated = True
    s = "\n".join(lines)
    if len(s) > _BODY_MAX_CHARS:
        s = s[:_BODY_MAX_CHARS]
        truncated = True
    if truncated:
        s = f"{s}\n… (truncated)"
    return Panel(Text(s), border_style=style, padding=(0, 1))
