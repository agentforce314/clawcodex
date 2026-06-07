"""Facade — agent/tool_authoring/call_handlers/bash.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.agent.tool_authoring.call_handlers.bash import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.agent.tool_authoring.call_handlers.bash`` directly.
"""

from clawcodex_ext.agent.tool_authoring.call_handlers.bash import (  # noqa: F401
    BashCallError,
    execute_bash,
)

__all__ = [
    "BashCallError",
    "execute_bash",
]
