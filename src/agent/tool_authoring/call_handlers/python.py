"""Facade — agent/tool_authoring/call_handlers/python.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.agent.tool_authoring.call_handlersthon import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.agent.tool_authoring.call_handlersthon`` directly.
"""

from clawcodex_ext.agent.tool_authoring.call_handlersthon import (  # noqa: F401
    PythonCallError,
    execute_python,
)

__all__ = [
    "PythonCallError",
    "execute_python",
]
