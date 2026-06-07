"""Facade — tool_system/tools/progress_report.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.tool_system.tools.progress_report import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.tool_system.tools.progress_report`` directly.
"""

from clawcodex_ext.tool_system.tools.progress_report import (  # noqa: F401
    ProgressReportTool,
)

__all__ = [
    "ProgressReportTool",
]
