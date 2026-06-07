"""Facade — agent/tool_authoring/persistence.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.agent.tool_authoring.persistence import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.agent.tool_authoring.persistence`` directly.
"""

from clawcodex_ext.agent.tool_authoring.persistence import (  # noqa: F401
    TOOL_DIR,
    save_spec,
    load_spec,
    delete_spec,
    list_persisted_specs,
    clear_persisted,
)

__all__ = [
    "TOOL_DIR",
    "save_spec",
    "load_spec",
    "delete_spec",
    "list_persisted_specs",
    "clear_persisted",
]
