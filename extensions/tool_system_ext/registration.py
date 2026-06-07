"""Extension tool registration.

Registers 二开 tools that are not part of upstream's ALL_STATIC_TOOLS.
Called by ``src/tool_system/defaults.py:build_default_registry``.
"""

from __future__ import annotations

from src.tool_system.build_tool import Tool

from clawcodex_ext.tool_system.tools.progress_report import ProgressReportTool
from clawcodex_ext.tool_system.tools.task_directives import TaskDirectivesTool
from clawcodex_ext.tool_system.tools.task_inspect import TaskInspectTool

EXTENSION_TOOLS: list[Tool] = [
    ProgressReportTool,
    TaskDirectivesTool,
    TaskInspectTool,
]

__all__ = [
    "EXTENSION_TOOLS",
]
