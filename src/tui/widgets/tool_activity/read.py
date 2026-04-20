"""Read tool activity."""

from __future__ import annotations

from typing import Any

from rich.text import Text

from .base import ToolActivity, truncated_panel


class ReadActivity(ToolActivity):
    def inflight_text(self) -> Text:
        path = self.tool_input.get("file_path") or self.tool_input.get("filePath") or ""
        return Text(f"read {path}" if path else "read …", style="dim")

    def result_body(self, output: Any, *, is_error: bool) -> Any | None:
        if not isinstance(output, dict):
            return None
        f = output.get("file")
        if isinstance(f, dict):
            content = f.get("content")
            if isinstance(content, str) and content.strip():
                return truncated_panel(content, style="red" if is_error else "green")
        return None
