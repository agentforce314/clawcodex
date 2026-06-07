"""Facade — agent/tool_authoring/registry_ext.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.agent.tool_authoring.registry_ext import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.agent.tool_authoring.registry_ext`` directly.
"""

from clawcodex_ext.agent.tool_authoring.registry_ext import (  # noqa: F401
    add_tool,
    get_tool,
    list_tools,
    remove_tool,
    clear,
)

__all__ = [
    "add_tool",
    "get_tool",
    "list_tools",
    "remove_tool",
    "clear",
]
