"""Context window sizes matching TypeScript model/context.ts."""

from __future__ import annotations

import logging
import os

from .configs import get_model_config

logger = logging.getLogger(__name__)

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


def resolve_max_output_tokens(
    override: int | None, model_id: str | None
) -> int:
    """Resolve the request-path ``max_tokens`` (ch04 round-3 G0).

    Precedence mirrors TS ``claude.ts:1602-1605``:
    1. explicit override (the query loop's 64K escalation passes through
       here unchanged);
    2. ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` env — the key has been on the
       trusted-env allowlist since round 1 (``trust_boundary.py``);
       consuming it closes that dangling promise. Invalid / non-positive
       values are ignored with a debug log;
    3. the per-model table via :func:`get_model_max_output_tokens`
       (→ ``DEFAULT_MAX_OUTPUT_TOKENS`` 8_192 for unknown models).

    Port decision vs TS: TS gates an 8_000 cap behind a remote flag with
    a 32_000 literal default (``utils/context.ts:28,38``,
    ``claude.ts:3417-3424``); the port has no remote-flag tier, so the
    per-model table is the single source. Before this function existed,
    normal requests silently went out at the provider-default 4096 — the
    chapter's "8K-class default + one 64K retry" economics were not on
    the wire.
    """
    if override is not None:
        return override
    raw = os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
    if raw:
        try:
            value = int(raw.strip())
        except ValueError:
            value = 0
        if value > 0:
            return value
        logger.debug(
            "ignoring invalid CLAUDE_CODE_MAX_OUTPUT_TOKENS=%r", raw
        )
    if model_id:
        return get_model_max_output_tokens(model_id)
    return DEFAULT_MAX_OUTPUT_TOKENS
