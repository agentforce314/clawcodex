"""Downstream provider extensions — model discovery hooks and provider overrides."""
from __future__ import annotations

# Note: ``register_provider`` and ``register_provider_info`` are safe to call
# at module level because they only touch the PROVIDER_INFO dict and the
# _EXTRA_PROVIDER_CLASSES dict — they do NOT import any provider class
# immediately (avoiding the circular-import chain:
#   src.auth.codex_oauth → clawcodex_ext → … → src.auth.codex_oauth).

from clawcodex_ext.providers.factory import register_provider, register_provider_info

from clawcodex_ext.providers.hooks import _codex_api_discovery
from clawcodex_ext.cli.model_cmd.registry import register_discovery_hook

register_discovery_hook("openai-codex", _codex_api_discovery)


def _OpenAICodexProvider_lazy():
    """Lazy accessor that defers the import until first use.

    We cannot import OpenAICodexProvider at module level because it
    depends on src.auth.codex_oauth which may trigger a circular import.
    Instead, register a callable that returns the class on demand.
    """
    from clawcodex_ext.providers.openai_codex_provider import OpenAICodexProvider
    return OpenAICodexProvider


# Register openai-codex provider info via the generic extension API
# rather than hardcoding it in src/providers/__init__.py.
# Use register_provider (not just register_provider_info) so that
# get_provider_class("openai-codex") also works via _EXTRA_PROVIDER_CLASSES.
register_provider(
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
    _OpenAICodexProvider_lazy,  # type: ignore[arg-type]
)
