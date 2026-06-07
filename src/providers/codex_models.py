"""Facade — providers/codex_models.py has been moved to clawcodex_ext.

This module re-exports the public API so that existing ``from
src.providers.codex_models import …`` call sites continue to work
during the migration.  New code should import from
``clawcodex_ext.providers.codex_models`` directly.
"""

from clawcodex_ext.providers.codex_models import (  # noqa: F401
    CODEX_FALLBACK_MODELS,
    CODEX_MODELS_URL,
    get_codex_model_ids,
)

__all__ = [
    "CODEX_FALLBACK_MODELS",
    "CODEX_MODELS_URL",
    "get_codex_model_ids",
]
