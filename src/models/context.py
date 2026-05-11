"""Context window sizes matching TypeScript model/context.ts."""

from __future__ import annotations

from .configs import get_model_config

# Default context window for unknown models
DEFAULT_CONTEXT_WINDOW = 200_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_192

# WI-5.3: 1M context window via ``[1m]`` model-id suffix. Mirrors TS
# ``utils/context.ts:54-55,98-100,129-134``. The suffix is a user-facing
# opt-in (e.g., ``claude-opus-4-7[1m]``) on models that support 1M tokens.
# At resolution time the suffix is stripped before the id reaches the API.
ONE_MILLION_CONTEXT_TOKENS = 1_000_000
ONE_MILLION_SUFFIX = "[1m]"


def has_1m_context_suffix(model_id: str) -> bool:
    """True if ``model_id`` ends with the opt-in ``[1m]`` suffix."""
    return isinstance(model_id, str) and model_id.endswith(ONE_MILLION_SUFFIX)


def strip_1m_context_suffix(model_id: str) -> str:
    """Strip the ``[1m]`` suffix if present. Use before sending to the API.

    The Anthropic API doesn't accept ``[1m]`` in the model field — it's a
    Python-side opt-in marker. Strip before forwarding.
    """
    if has_1m_context_suffix(model_id):
        return model_id[: -len(ONE_MILLION_SUFFIX)]
    return model_id


def get_context_window_for_model(model_id: str) -> int:
    """Get the context window size for a model.

    Recognizes the ``[1m]`` opt-in suffix (WI-5.3) and returns 1_000_000
    for any model with the suffix. Otherwise falls back to the per-model
    config or the default 200K.
    """
    if has_1m_context_suffix(model_id):
        return ONE_MILLION_CONTEXT_TOKENS
    config = get_model_config(model_id)
    if config:
        return config.context_window
    return DEFAULT_CONTEXT_WINDOW


def get_model_max_output_tokens(model_id: str) -> int:
    """Get the maximum output tokens for a model.

    The ``[1m]`` suffix doesn't change max_output_tokens (only the input
    context window). Strip the suffix and look up the underlying model's
    output cap.
    """
    base_id = strip_1m_context_suffix(model_id)
    config = get_model_config(base_id)
    if config:
        return config.max_output_tokens
    return DEFAULT_MAX_OUTPUT_TOKENS
