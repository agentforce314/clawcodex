"""Facade — tool_system/tools/task_inspect.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.tool_system.tools.task_inspect import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.tool_system.tools.task_inspect`` directly.
"""

from clawcodex_ext.tool_system.tools.task_inspect import (  # noqa: F401
    TaskInspectTool,
)

__all__ = [
    "TaskInspectTool",
]
