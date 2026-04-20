"""Edit tool activity — renders an inline diff of the applied change.

Mirrors the body of Claude Code's ``Update`` tool result row: a one-line
``Added X lines, removed Y lines`` summary above the line-numbered
unified diff.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from ..structured_diff import (
    count_changes,
    parse_structured_patch,
    render_diff,
)
from .base import ToolActivity


class EditActivity(ToolActivity):
    def inflight_text(self) -> Text:
        path = self.tool_input.get("file_path") or self.tool_input.get("filePath") or ""
        return Text(f"edit {path}" if path else "edit …", style="dim")

    def result_body(self, output: Any, *, is_error: bool) -> Any | None:
        if is_error or not isinstance(output, dict):
            return None

        hunks = output.get("structuredPatch") or []
        diff_lines = parse_structured_patch(hunks)
        adds, removes = count_changes(diff_lines)
        summary = _format_edit_summary(adds, removes)

        if diff_lines:
            return Group(Text(summary, style="dim"), render_diff(diff_lines))

        # Fallback for create-type results (no patch): keep the row from
        # going blank by surfacing the file path.
        path = output.get("filePath") or output.get("file_path") or ""
        if path:
            return Text(path, style="green")
        return None


def _format_edit_summary(adds: int, removes: int) -> str:
    """Format an "Added X lines, removed Y lines" summary.

    Mirrors the pluralization in the TS reference component
    (``FileEditToolUpdatedMessage.tsx``): when both counts are non-zero
    the second clause uses lowercase ``removed`` because it follows a
    comma; standalone clauses are sentence-cased.
    """

    if adds <= 0 and removes <= 0:
        return ""
    parts: list[str] = []
    if adds > 0:
        parts.append(f"Added {adds} {'line' if adds == 1 else 'lines'}")
    if removes > 0:
        verb = "Removed" if adds == 0 else "removed"
        parts.append(f"{verb} {removes} {'line' if removes == 1 else 'lines'}")
    return ", ".join(parts)
