"""Facade — tool_system/tools/task_directives.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.tool_system.tools.task_directives import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.tool_system.tools.task_directives`` directly.
"""

from clawcodex_ext.tool_system.tools.task_directives import (  # noqa: F401
    TaskDirectivesTool,
)

__all__ = [
    "TaskDirectivesTool",
]
