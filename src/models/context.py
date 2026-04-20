"""Context window sizes matching TypeScript model/context.ts."""

from __future__ import annotations

from .configs import get_model_config

# Default context window for unknown models
DEFAULT_CONTEXT_WINDOW = 200_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_192


def get_context_window_for_model(model_id: str) -> int:
    """Get the context window size for a model."""
    config = get_model_config(model_id)
    if config:
        return config.context_window
    return DEFAULT_CONTEXT_WINDOW


def get_model_max_output_tokens(model_id: str) -> int:
    """Get the maximum output tokens for a model."""
    config = get_model_config(model_id)
    if config:
        return config.max_output_tokens
    return DEFAULT_MAX_OUTPUT_TOKENS
