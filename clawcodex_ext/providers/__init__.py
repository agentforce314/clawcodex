"""Downstream provider extensions — model discovery hooks and provider overrides."""
from __future__ import annotations

# Note: ``register_provider_info`` is safe to call at module level because
# it only touches the ``PROVIDER_INFO`` dict and does NOT import any
# provider class (avoiding the circular-import chain:
#   src.auth.codex_oauth → clawcodex_ext → … → src.auth.codex_oauth)
# The ``get_provider_class("openai-codex")`` mapping remains hardcoded
# in ``src/providers/__init__.py`` for the same reason.

from src.providers import register_provider_info

from clawcodex_ext.providers.hooks import _codex_api_discovery
from clawcodex_ext.cli.model_cmd.registry import register_discovery_hook

register_discovery_hook("openai-codex", _codex_api_discovery)

# Register openai-codex provider info via the generic extension API
# rather than hardcoding it in src/providers/__init__.py.
register_provider_info(
    "openai-codex",
    {
        "label": "OpenAI Codex (ChatGPT OAuth)",
        "default_base_url": "https://chatgpt.com/backend-api/codex",
        "default_model": "gpt-5.3-codex",
        "available_models": [
            "gpt-5.3-codex",
            "gpt-5.3-codex-spark",
        ],
    },
)
