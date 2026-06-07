"""Facade — agent/tool_authoring/__init__.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.agent.tool_authoring.__init__ import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.agent.tool_authoring.__init__`` directly.
"""

from clawcodex_ext.agent.tool_authoring.__init__ import (  # noqa: F401
    AgentToolSpec,
    ValidationError,
    validate_spec,
    build_tool_from_spec,
    create_and_validate,
    add_tool,
    get_tool,
    list_tools,
    remove_tool,
    clear,
    save_spec,
    load_spec,
    delete_spec,
    list_persisted_specs,
    clear_persisted,
    register_python_function,
    list_python_functions,
    TOOL_DIR,
)

__all__ = [
    "AgentToolSpec",
    "ValidationError",
    "validate_spec",
    "build_tool_from_spec",
    "create_and_validate",
    "add_tool",
    "get_tool",
    "list_tools",
    "remove_tool",
    "clear",
    "save_spec",
    "load_spec",
    "delete_spec",
    "list_persisted_specs",
    "clear_persisted",
    "register_python_function",
    "list_python_functions",
    "TOOL_DIR",
]
