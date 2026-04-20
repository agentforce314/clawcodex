"""Model resolution matching TypeScript model/model.ts."""

from __future__ import annotations

import logging
from typing import Any

from .aliases import resolve_alias
from .configs import get_model_config

logger = logging.getLogger(__name__)


def resolve_model(name: str) -> str:
    """Resolve a model name/alias to its canonical form.

    Steps:
    1. Resolve alias (e.g. "sonnet" → "claude-sonnet-4-20250514")
    2. Return canonical name
    """
    canonical = resolve_alias(name)
    config = get_model_config(canonical)
    if config and config.is_deprecated:
        logger.warning(
            "Model %s is deprecated: %s", canonical, config.deprecation_message
        )
    return canonical


def canonical_model_name(name: str) -> str:
    """Get the canonical model name (same as resolve_model)."""
    return resolve_model(name)


def display_name(model_id: str) -> str:
    """Get a human-readable display name for a model."""
    config = get_model_config(model_id)
    if config:
        return config.display_name
    # Fallback: title-case the model ID
    return model_id.replace("-", " ").title()


def deprecation_warning(model_id: str) -> str | None:
    """Get deprecation warning for a model, or None if not deprecated."""
    config = get_model_config(model_id)
    if config and config.is_deprecated:
        return config.deprecation_message
    return None
