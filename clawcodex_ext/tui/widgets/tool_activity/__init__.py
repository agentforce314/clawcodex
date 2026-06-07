"""Per-tool activity widgets.

Mirrors the dispatch in ``typescript/src/components/tasks/renderToolActivity.tsx``
— each tool kind has a dedicated renderer that knows how to summarise
its input / output. The router :func:`build_tool_activity` picks the
right widget from the tool name; unknown tools fall back to
:class:`~src.tui.widgets.tool_activity.default.DefaultToolActivity`.

All widgets share a common API:

* ``on_result(output, *, is_error, error)`` — called once by the owning
  :class:`AssistantToolUseMessage` when the agent loop finishes the
  tool, switching the body from its in-flight preview to the final
  summary.
"""

from __future__ import annotations

from .base import ToolActivity
from .default import DefaultToolActivity
from .bash import BashActivity
from .read import ReadActivity
from .write import WriteActivity
from .edit import EditActivity
from .grep import GrepActivity
from .glob import GlobActivity
from .task import TaskActivity


_TOOL_MAP: dict[str, type[ToolActivity]] = {
    "bash": BashActivity,
    "read": ReadActivity,
    "write": WriteActivity,
    "edit": EditActivity,
    "grep": GrepActivity,
    "glob": GlobActivity,
    "task": TaskActivity,
}


def build_tool_activity(tool_name: str, tool_input: dict) -> ToolActivity:
    """Factory that returns the right widget for a tool.

    Falls back to :class:`DefaultToolActivity` when the name is unknown
    (custom / MCP tools) — the fallback renders a compact JSON preview
    of the input which keeps the transcript useful without per-tool
    styling.
    """

    cls = _TOOL_MAP.get((tool_name or "").lower(), DefaultToolActivity)
    return cls(tool_name=tool_name, tool_input=tool_input or {})


__all__ = [
    "ToolActivity",
    "DefaultToolActivity",
    "BashActivity",
    "ReadActivity",
    "WriteActivity",
    "EditActivity",
    "GrepActivity",
    "GlobActivity",
    "TaskActivity",
    "build_tool_activity",
]
