"""Fallback activity widget for tools without a dedicated renderer.

Used for MCP tools and any future built-in that doesn't have a
handcrafted component yet. Mirrors ``GroupedToolUseContent`` from the
ink reference: compact summary on the row plus a truncated JSON-ish
body for the result.
"""

from __future__ import annotations

import json
from typing import Any

from rich.text import Text

from .base import ToolActivity, truncated_panel


class DefaultToolActivity(ToolActivity):
    """Generic renderer for tools with no bespoke component."""

    def inflight_text(self) -> Text:
        preview = _compact(self.tool_input)
        return Text(f"…{preview}" if preview else "…", style="dim")

    def result_body(self, output: Any, *, is_error: bool) -> Any | None:
        if output is None:
            return None
        style = "red" if is_error else "green"
        if isinstance(output, str):
            if not output.strip():
                return None
            return truncated_panel(output, style=style)
        try:
            rendered = json.dumps(output, indent=2, ensure_ascii=False)
        except Exception:
            rendered = repr(output)
        return truncated_panel(rendered, style=style)


def _compact(tool_input: dict) -> str:
    """A one-line preview of the tool input for the inflight placeholder."""
    if not tool_input:
        return ""
    items: list[str] = []
    for key, value in tool_input.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            sv = "…"
        else:
            sv = str(value)
            if len(sv) > 40:
                sv = sv[:37] + "…"
        items.append(f"{key}={sv}")
        if len(items) >= 3:
            break
    return " ".join(items)
