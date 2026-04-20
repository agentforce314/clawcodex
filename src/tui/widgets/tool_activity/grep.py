"""Grep tool activity."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from .base import ToolActivity, truncated_panel


class GrepActivity(ToolActivity):
    def inflight_text(self) -> Text:
        pattern = self.tool_input.get("pattern") or ""
        path = self.tool_input.get("path") or ""
        tail = f" in {path}" if path else ""
        return Text(f"grep /{pattern}/{tail}" if pattern else "grep …", style="dim")

    def result_body(self, output: Any, *, is_error: bool) -> Any | None:
        if not isinstance(output, dict):
            return None
        content = output.get("content")
        if isinstance(content, str) and content.strip():
            return truncated_panel(content, style="red" if is_error else "green")
        n = output.get("numFiles")
        mode = output.get("mode")
        if n is not None:
            return Text(
                f"mode={mode} · files={n}",
                style="red" if is_error else "green",
            )
        return None
