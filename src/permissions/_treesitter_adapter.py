"""Facade — permissions/_treesitter_adapter.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.permissions._treesitter_adapter import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.permissions._treesitter_adapter`` directly.
"""

from clawcodex_ext.permissions._treesitter_adapter import (  # noqa: F401
    is_bashlex_available,
    BashlexParseResult,
    parse_command_with_bashlex,
    classify_command_with_bashlex,
    get_command_safety_from_bashlex,
)

__all__ = [
    "is_bashlex_available",
    "BashlexParseResult",
    "parse_command_with_bashlex",
    "classify_command_with_bashlex",
    "get_command_safety_from_bashlex",
]
