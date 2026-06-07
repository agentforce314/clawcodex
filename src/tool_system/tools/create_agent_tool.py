"""Facade — tool_system/tools/create_agent_tool.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.tool_system.tools.create_agent_tool import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.tool_system.tools.create_agent_tool`` directly.
"""

from clawcodex_ext.tool_system.tools.create_agent_tool import (  # noqa: F401
    CREATE_AGENT_TOOL_NAME,
    CREATE_AGENT_INPUT_SCHEMA,
    make_create_agent_tool,
    load_persisted_agent_tools,
)

__all__ = [
    "CREATE_AGENT_TOOL_NAME",
    "CREATE_AGENT_INPUT_SCHEMA",
    "make_create_agent_tool",
    "load_persisted_agent_tools",
]
