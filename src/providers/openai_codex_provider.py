"""Facade — providers/openai_codex_provider.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.providers.openai_codex_provider import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.providers.openai_codex_provider`` directly.
"""

from clawcodex_ext.providers.openai_codex_provider import (  # noqa: F401
    OpenAICodexProvider,
)

__all__ = [
    "OpenAICodexProvider",
]
