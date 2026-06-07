"""Facade — agent/tool_authoring/spec.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.agent.tool_authoring.spec import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.agent.tool_authoring.spec`` directly.
"""

from clawcodex_ext.agent.tool_authoring.spec import (  # noqa: F401
    AgentToolSpec,
)

__all__ = [
    "AgentToolSpec",
]
