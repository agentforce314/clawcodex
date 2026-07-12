"""LLM Providers for Claw Codex."""

from __future__ import annotations

from typing import Any, TypedDict

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
            # GPT-5.5 (flagship; also served by the ChatGPT subscription)
            "gpt-5.5",
            # GPT-5.4 series
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
            "gpt-5.3-codex-spark",
            # Legacy GPT-4 series
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo",
        ],
    },
    "zai": {
        "label": "Z.ai (GLM Coding)",
        "default_base_url": "https://api.z.ai/api/coding/paas/v4",
        "default_model": "GLM-5.1",
        "available_models": [
            # GLM Coding Plan (Z.ai direct, OpenAI-compatible)
            "GLM-5.1",  # stable default
            "GLM-5.2",  # opt-in preview
        ],
    },
    "minimax": {
        "label": "Minimax AI",
        "default_base_url": "https://api.minimax.io/anthropic",
        "default_model": "MiniMax-M3",
        "available_models": [
            "MiniMax-M3",
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


# --- Data-driven OpenAI-compatible providers ------------------------------
# The bulk of LLM vendors speak the OpenAI ``/chat/completions`` wire format and
# differ only in base URL / default model / API-key env vars. Those live in a
# spec registry (``src/providers/openai_compatible_specs.py``) and are merged in
# here so they appear in ``login``, default config, model pickers, and the
# defaults table exactly like the hand-written providers. Hand-written entries
# above win on any id collision (there are none today — the registry holds only
# the providers ClawCodex previously lacked).
from .openai_compatible_specs import (  # noqa: E402
    SPECS_BY_ID as _SPECS_BY_ID,
    build_provider_class as _build_spec_provider_class,
    spec_aliases as _spec_aliases,
    spec_provider_info as _spec_provider_info,
)

for _spec_id, _spec_info in _spec_provider_info().items():
    PROVIDER_INFO.setdefault(_spec_id, _spec_info)  # type: ignore[arg-type]


# Legacy / alternate provider names accepted during resolution. ``glm`` is the
# pre-rename id for Z.ai (Zhipu rebranded as z.ai); ``z-ai`` / ``z_ai`` /
# ``z.ai`` are the commonly-written spellings. Aliases are normalized in
# ``get_provider_class`` / ``get_provider_info`` only — config lookups
# (``get_provider_config``) stay literal so a ``[providers.glm]`` block written
# before the rename still resolves by its own key.
PROVIDER_ALIASES: dict[str, str] = {
    "glm": "zai",
    "z-ai": "zai",
    "z_ai": "zai",
    "z.ai": "zai",
}

# Merge registry aliases (e.g. ``nim`` -> ``nvidia-nim``, ``kimi`` ->
# ``moonshot``). Literal entries above win on collision.
for _alias, _canonical in _spec_aliases().items():
    PROVIDER_ALIASES.setdefault(_alias, _canonical)


def _canonical_provider_name(provider_name: str) -> str:
    """Resolve a legacy/alternate provider spelling to its canonical id."""
    return PROVIDER_ALIASES.get(provider_name, provider_name)


def canonical_provider_name(provider_name: str) -> str:
    """Public alias for :func:`_canonical_provider_name`.

    Resolves a legacy/alternate spelling (``glm``, ``z.ai``, ``nim``,
    ``kimi`` …) to its canonical provider id. Used by ``src.config`` so
    ``--provider <alias>`` resolves a config block, and by the entrypoints'
    API-key resolution.
    """
    return _canonical_provider_name(provider_name)


def get_provider_info(provider_name: str) -> ProviderInfo:
    """Get provider info by name (legacy aliases accepted)."""
    canonical = _canonical_provider_name(provider_name)
    if canonical not in PROVIDER_INFO:
        raise ValueError(f"Unknown provider: {provider_name}")
    return PROVIDER_INFO[canonical]


def get_provider_class(provider_name: str):
    """Get provider class by name (legacy aliases accepted)."""
    provider_name = _canonical_provider_name(provider_name)
    if provider_name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider
    if provider_name == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider
    if provider_name == "zai":
        from .zai_provider import ZaiProvider

        return ZaiProvider
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
    # Data-driven OpenAI-compatible providers (nvidia-nim, together, novita,
    # moonshot, ollama, …). The class is synthesized from the provider's spec.
    if provider_name in _SPECS_BY_ID:
        return _build_spec_provider_class(provider_name)
    raise ValueError(f"Unknown provider: {provider_name}")


# API-key env-var candidates for the hand-written providers. The registry
# providers carry their own ``env_vars`` (see ``openai_compatible_specs``);
# these cover the classes that predate the registry so env-var resolution
# (:func:`resolve_api_key`) is uniform across every provider — each value is
# the vendor's conventional API-key environment variable.
_BUILTIN_ENV_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "zai": ("ZAI_API_KEY", "Z_AI_API_KEY"),
    "minimax": ("MINIMAX_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def provider_env_vars(provider_name: str) -> tuple[str, ...]:
    """API-key environment-variable candidates for a provider (highest first)."""
    canonical = _canonical_provider_name(provider_name)
    if canonical in _BUILTIN_ENV_VARS:
        return _BUILTIN_ENV_VARS[canonical]
    spec = _SPECS_BY_ID.get(canonical)
    return spec.env_vars if spec else ()


def provider_requires_api_key(provider_name: str) -> bool:
    """Whether a provider needs an API key.

    ``False`` for local servers (Ollama / vLLM / SGLang) that accept any/no
    token, so they stay usable without running ``login``. Every other provider
    requires a key.
    """
    canonical = _canonical_provider_name(provider_name)
    spec = _SPECS_BY_ID.get(canonical)
    return spec.requires_api_key if spec else True


def provider_has_credentials(provider_name: str, api_key: str) -> bool:
    """Whether requests to ``provider_name`` can authenticate.

    True when an API key is present, the provider needs none (local
    servers), or the user has a stored subscription OAuth login — Claude
    Pro/Max for ``anthropic`` (#697) or a ChatGPT plan for ``openai``.
    Every "no API key configured" fatality gate must go through this
    helper (startup validation, agent-server session init, agent-server
    provider switch) so subscription logins work on all of them —
    gating on ``provider_requires_api_key`` alone bricked subscription
    sessions in the TUI.
    """
    if api_key or not provider_requires_api_key(provider_name):
        return True
    canonical = _canonical_provider_name(provider_name)
    if canonical == "anthropic":
        from src.auth.anthropic_subscription import load_credentials

        return load_credentials() is not None
    if canonical == "openai":
        from src.auth.openai_subscription import load_credentials

        return load_credentials() is not None
    return False


def resolve_api_key(
    provider_name: str, provider_cfg: dict[str, Any] | None = None
) -> str:
    """Resolve a provider's API key: configured value first, then env vars.

    The configured ``providers.<name>.api_key`` always wins. When it is empty
    (the common case for a freshly-added provider the user hasn't run ``login``
    for), fall back to the provider's known env-var candidates via
    :func:`src.secret_store.get_secret` — which itself checks the real process
    environment, then the global config ``env`` block. This makes every
    provider, including the registry additions, usable by simply exporting e.g.
    ``TOGETHER_API_KEY`` without hand-editing ``config.json``.

    Returns ``""`` when no key is found; callers gate fatality on
    :func:`provider_requires_api_key`.
    """
    if provider_cfg is None:
        try:
            from src.config import get_provider_config

            provider_cfg = get_provider_config(provider_name)
        except Exception:
            provider_cfg = {}
    configured = (provider_cfg or {}).get("api_key")
    if isinstance(configured, str) and configured.strip():
        return configured
    from src.secret_store import get_secret

    for env_name in provider_env_vars(provider_name):
        value = get_secret(env_name)
        if value and value.strip():
            return value
    return ""


# Legacy registry for display purposes
AVAILABLE_PROVIDERS: dict[str, str] = {k: v["label"] for k, v in PROVIDER_INFO.items()}


__all__ = [
    "BaseProvider",
    "ChatMessage",
    "ChatResponse",
    "get_provider_class",
    "get_provider_info",
    "canonical_provider_name",
    "provider_env_vars",
    "provider_has_credentials",
    "provider_requires_api_key",
    "resolve_api_key",
    "PROVIDER_INFO",
    "PROVIDER_ALIASES",
    "AVAILABLE_PROVIDERS",
]
