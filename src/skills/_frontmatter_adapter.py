"""Facade — skills/_frontmatter_adapter.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.skills._frontmatter_adapter import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.skills._frontmatter_adapter`` directly.
"""

from clawcodex_ext.skills._frontmatter_adapter import (  # noqa: F401
    is_frontmatter_available,
    parse_frontmatter_with_library,
)

__all__ = [
    "is_frontmatter_available",
    "parse_frontmatter_with_library",
]
