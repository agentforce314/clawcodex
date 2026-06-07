"""Task / sub-agent tool activity.

Mirrors ``components/tasks/BackgroundTask.tsx`` at a reduced fidelity —
we only display description + agent type here; the live task list and
agent-progress feed from ``TaskListV2`` ship in Phase 3.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text

from .base import ToolActivity, truncated_panel


class TaskActivity(ToolActivity):
    def inflight_text(self) -> Text:
        desc = self.tool_input.get("description") or ""
        agent = self.tool_input.get("agent_type") or self.tool_input.get("subagent_type") or ""
        if agent and desc:
            return Text(f"{agent}: {desc}", style="dim")
        if desc:
            return Text(desc, style="dim")
        if agent:
            return Text(agent, style="dim")
        return Text("task …", style="dim")

    def result_body(self, output: Any, *, is_error: bool) -> Any | None:
        if isinstance(output, str) and output.strip():
            return truncated_panel(output, style="red" if is_error else "green")
        if isinstance(output, dict):
            text = output.get("text") or output.get("response") or ""
            if isinstance(text, str) and text.strip():
                return truncated_panel(text, style="red" if is_error else "green")
        return None
