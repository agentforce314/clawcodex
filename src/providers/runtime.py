"""Runtime provider construction helpers."""

from __future__ import annotations

from src.auth.codex_oauth import CodexAuthError, resolve_codex_runtime_credentials
from src.config import get_provider_config
from src.providers import create_provider

from .base import BaseProvider

OAUTH_PROVIDERS = {"openai-codex"}


def build_provider_from_config(provider_name: str, model: str | None = None) -> BaseProvider:
    try:
        provider_cfg = get_provider_config(provider_name)
    except ValueError:
        provider_cfg = {}
    selected_model = model or provider_cfg.get("default_model")

    if provider_name == "openai-codex":
        try:
            credentials = resolve_codex_runtime_credentials()
        except CodexAuthError as exc:
            raise RuntimeError(
                f"OpenAI Codex is not authenticated. Run `clawcodex login` and select openai-codex. ({exc})"
            ) from exc
        return create_provider(
            provider_name,
            api_key=credentials.api_key,
            base_url=provider_cfg.get("base_url") or credentials.base_url,
            model=selected_model,
        )

    if not provider_cfg.get("api_key"):
        # If provider has no config entry at all (unknown provider), allow
        # api_key=None — the provider implementation will handle the missing
        # key (e.g. ollama runs locally without auth).
        if provider_cfg:  # known provider with missing config
            raise RuntimeError(
                f"API key for provider '{provider_name}' is not configured. Run `clawcodex login` to set it up."
            )
        api_key = None
    else:
        api_key = provider_cfg["api_key"]
    return create_provider(
        provider_name,
        api_key=api_key,
        base_url=provider_cfg.get("base_url"),
        model=selected_model,
    )
