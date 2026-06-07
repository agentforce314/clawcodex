"""Facade — agent/tool_authoring/call_handlers/http.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.agent.tool_authoring.call_handlers.http import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.agent.tool_authoring.call_handlers.http`` directly.
"""

from clawcodex_ext.agent.tool_authoring.call_handlers.http import (  # noqa: F401
    HttpCallError,
    execute_http,
)

__all__ = [
    "HttpCallError",
    "execute_http",
]
