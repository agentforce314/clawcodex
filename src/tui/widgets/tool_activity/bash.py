"""Bash tool activity — parity with ``ShellProgress`` / ``BashProgress``.

Inflight: shows the command being executed (truncated) so the user can
tell what's running.  Completion: renders stdout (or stderr on failure)
in a bordered preview panel with the usual truncation limits.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text

from .base import ToolActivity, truncated_panel


class BashActivity(ToolActivity):
    def inflight_text(self) -> Text:
        cmd = (self.tool_input.get("command") or "").strip()
        if len(cmd) > 120:
            cmd = cmd[:117] + "…"
        return Text(f"$ {cmd}" if cmd else "$ …", style="dim")

    def result_body(self, output: Any, *, is_error: bool) -> Any | None:
        if not isinstance(output, dict):
            return None
        stdout = output.get("stdout") or ""
        stderr = output.get("stderr") or ""
        body = stdout or stderr
        if not body or not body.strip():
            return None
        style = "red" if is_error else "green"
        return truncated_panel(body, style=style)
