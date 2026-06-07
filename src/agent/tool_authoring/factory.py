"""Facade — agent/tool_authoring/factory.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.agent.tool_authoring.factory import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.agent.tool_authoring.factory`` directly.
"""

from clawcodex_ext.agent.tool_authoring.factory import (  # noqa: F401
    logger,
    build_tool_from_spec,
    create_and_validate,
)

__all__ = [
    "logger",
    "build_tool_from_spec",
    "create_and_validate",
]
