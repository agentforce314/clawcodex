"""Facade — tool_system/tools/ask_issue_author.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.tool_system.tools.ask_issue_author import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.tool_system.tools.ask_issue_author`` directly.
"""

from clawcodex_ext.tool_system.tools.ask_issue_author import (  # noqa: F401
    AskIssueAuthorTool,
)

__all__ = [
    "AskIssueAuthorTool",
]
