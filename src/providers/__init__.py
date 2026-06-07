"""LLM Providers for Claw Codex."""

from __future__ import annotations

from typing import TypedDict

from .base import BaseProvider, ChatMessage, ChatResponse


# Provider metadata for login/UI
class ProviderInfo(TypedDict):
    label: str
    default_base_url: str
    default_model: str
    available_models: list[str]


PROVIDER_INFO: dict[str, ProviderInfo] = {
    "anthropic": {
        "label": "Anthropic Claude",
        "default_base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-6",
        "available_models": [
            # Claude 4 series (latest)
            "claude-sonnet-4-6",
            "claude-sonnet-4-5",
            "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-0",
            "claude-sonnet-4-20250514",
            "claude-opus-4-6",
            "claude-opus-4-5",
            "claude-opus-4-5-20251101",
            "claude-opus-4-1",
            "claude-opus-4-1-20250805",
            "claude-opus-4-0",
            "claude-opus-4-20250514",
            "claude-haiku-4-5",
            "claude-haiku-4-5-20251001",
            # Legacy
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ],
    },
    "openai": {
        "label": "OpenAI GPT",
        "default_base_url": "https://api.openai.com/v1",
        "default_model": "gpt-5.4",
        "available_models": [
            # GPT-5.4 series (latest flagship)
            "gpt-5.4",
            "gpt-5.4-pro",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            # GPT-5.2 series (previous)
            "gpt-5.2",
            "gpt-5.2-pro",
            "gpt-5.2-mini",
            "gpt-5.2-nano",
            # GPT-5.3-Codex (coding-specialized)
            "gpt-5.3-codex",
            # Legacy GPT-4 series
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo",
        ],
    },
    "glm": {
        "label": "Zhipu GLM (z.ai)",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "zai/glm-5",
        "available_models": [
            # GLM-5 series (latest, requires zai/ prefix)
            "zai/glm-5",
            "zai/glm-5-turbo",
            # GLM-4 series (standard, zai/ prefix)
            "zai/glm-4",
            "zai/glm-4-plus",
            "zai/glm-4-air",
            "zai/glm-4-flash",
            "zai/glm-4.5",
            "zai/glm-4.6",
            "zai/glm-4.7",
            # GLM-3 series (legacy)
            "zai/glm-3-turbo",
        ],
    },
    "minimax": {
        "label": "Minimax AI",
        "default_base_url": "https://api.minimaxi.com/anthropic",
        "default_model": "MiniMax-M2.7",
        "available_models": [
            # M2 series (latest)
            "MiniMax-M2.7",
            "MiniMax-M2.7-highspeed",
            "MiniMax-M2.5",
            "MiniMax-M2.5-highspeed",
            "M2-her",
            # Historical
            "MiniMax-M2.1",
            "MiniMax-M2.1-highspeed",
            "MiniMax-M2",
        ],
    },
    "deepseek": {
        "label": "DeepSeek",
        "default_base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-pro",
        "available_models": [
            # V4 series (current)
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            # Legacy aliases (being deprecated; map to v4-flash modes)
            "deepseek-chat",
            "deepseek-reasoner",
        ],
    },
    "gemini": {
        "label": "Google Gemini",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-pro",
        "available_models": [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
        ],
    },
    "openrouter": {
        "label": "OpenRouter (multi-vendor proxy)",
        "default_base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-sonnet-4.5",
        "available_models": [
            # DeepSeek V4 (latest, strongest — top of the list)
            "deepseek/deepseek-v4-pro",
            "deepseek/deepseek-v4-flash",
            # Anthropic
            "anthropic/claude-sonnet-4.5",
            "anthropic/claude-opus-4.1",
            "anthropic/claude-haiku-4.5",
            "anthropic/claude-3.5-sonnet",
            "anthropic/claude-3.5-haiku",
            # OpenAI
            "openai/gpt-5",
            "openai/gpt-5-mini",
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "openai/o1",
            "openai/o1-mini",
            # Google
            "google/gemini-2.5-pro",
            "google/gemini-2.5-flash",
            "google/gemini-2.0-flash",
            # Meta
            "meta-llama/llama-3.3-70b-instruct",
            "meta-llama/llama-3.1-405b-instruct",
            # Mistral
            "mistralai/mistral-large",
            "mistralai/mixtral-8x22b-instruct",
            # DeepSeek (V3.x line — V4 is at top of list)
            "deepseek/deepseek-v3.2",
            "deepseek/deepseek-v3.2-speciale",
            "deepseek/deepseek-v3.1-terminus",
            "deepseek/deepseek-chat-v3.1",
            "deepseek/deepseek-r1-0528",
            # xAI
            "x-ai/grok-2",
        ],
    },
}


def get_provider_info(provider_name: str) -> ProviderInfo:
    """Get provider info by name."""
    if provider_name not in PROVIDER_INFO:
        raise ValueError(f"Unknown provider: {provider_name}")
    return PROVIDER_INFO[provider_name]


def should_use_litellm() -> bool:
    """Return whether runtime provider creation should use LiteLLM."""
    from os import getenv

    return getenv("CLAW_USE_LITELLM", "").lower() in {"1", "true", "yes", "on"}


def create_provider(provider_name: str, *args, **kwargs) -> BaseProvider:
    """Create a provider instance for runtime use."""
    if should_use_litellm():
        from extensions.providers_ext import create_litellm_provider

        return create_litellm_provider(provider_name, *args, **kwargs)

    try:
        provider_cls = get_provider_class(provider_name)
    except ValueError:
        # Unknown provider — fallback to LiteLLM
        from extensions.providers_ext import create_litellm_provider

        return create_litellm_provider(provider_name, *args, **kwargs)

    return provider_cls(*args, **kwargs)


def get_provider_class(provider_name: str):
    """Get provider class by name."""
    if provider_name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider
    if provider_name == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider
    if provider_name == "openai-codex":
        from .openai_codex_provider import OpenAICodexProvider

        return OpenAICodexProvider
    if provider_name == "glm":
        from .glm_provider import GLMProvider

        return GLMProvider
    if provider_name == "minimax":
        from .minimax_provider import MinimaxProvider

        return MinimaxProvider
    if provider_name == "openrouter":
        from .openrouter_provider import OpenRouterProvider

        return OpenRouterProvider
    if provider_name == "deepseek":
        from .deepseek_provider import DeepSeekProvider

        return DeepSeekProvider
    if provider_name == "gemini":
        from .gemini_provider import GeminiProvider

        return GeminiProvider
    if provider_name in _EXTRA_PROVIDER_CLASSES:
        return _EXTRA_PROVIDER_CLASSES[provider_name]
    raise ValueError(f"Unknown provider: {provider_name}")


# ---------------------------------------------------------------------------
# Extension registration API
# ---------------------------------------------------------------------------

# Lazy registry for providers added by clawcodex_ext / extensions.
_EXTRA_PROVIDER_CLASSES: dict[str, type] = {}


def register_provider(name: str, info: ProviderInfo, cls: type) -> None:
    """Register a new provider at runtime.

    Adds *info* to ``PROVIDER_INFO`` and registers *cls* so that
    ``get_provider_class(name)`` returns it.

    Idempotent: calling twice with the same *name* is a no-op
    (first registration wins).
    """
    register_provider_info(name, info)
    if name not in _EXTRA_PROVIDER_CLASSES:
        _EXTRA_PROVIDER_CLASSES[name] = cls
        # Rebuild the display dict so it reflects the new provider.
        global AVAILABLE_PROVIDERS  # noqa: PLW0603
        AVAILABLE_PROVIDERS = {k: v["label"] for k, v in PROVIDER_INFO.items()}


def register_provider_info(name: str, info: ProviderInfo) -> None:
    """Add or update *info* in ``PROVIDER_INFO`` without a class mapping.

    Useful when the provider is served by LiteLLM or another generic
    backend that doesn't have a dedicated ``BaseProvider`` subclass.

    Also refreshes ``AVAILABLE_PROVIDERS`` so the new provider shows up
    in UI/CLI listings.
    """
    if name not in PROVIDER_INFO:
        PROVIDER_INFO[name] = info
        global AVAILABLE_PROVIDERS  # noqa: PLW0603
        AVAILABLE_PROVIDERS = {k: v["label"] for k, v in PROVIDER_INFO.items()}


# Legacy registry for display purposes
AVAILABLE_PROVIDERS: dict[str, str] = {k: v["label"] for k, v in PROVIDER_INFO.items()}


__all__ = [
    "BaseProvider",
    "ChatMessage",
    "ChatResponse",
    "create_provider",
    "get_provider_class",
    "get_provider_info",
    "PROVIDER_INFO",
    "AVAILABLE_PROVIDERS",
    "should_use_litellm",
    "register_provider",
    "register_provider_info",
]
