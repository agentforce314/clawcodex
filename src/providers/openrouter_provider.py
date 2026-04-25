"""OpenRouter provider implementation.

OpenRouter exposes an OpenAI-compatible API at https://openrouter.ai/api/v1
that proxies models from many vendors (Anthropic, OpenAI, Google, Meta, etc.).
Model names follow ``vendor/model`` (e.g. ``anthropic/claude-sonnet-4.5``).
"""

from __future__ import annotations

from typing import Any, Optional

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from .openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter provider using the OpenAI SDK against the OpenRouter base URL."""

    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        """Initialize OpenRouter provider.

        Args:
            api_key: OpenRouter API key (sk-or-...)
            base_url: Base URL (optional, defaults to https://openrouter.ai/api/v1)
            model: Default model in ``vendor/model`` form (default: anthropic/claude-sonnet-4.5)
        """
        super().__init__(
            api_key,
            base_url or self.DEFAULT_BASE_URL,
            model or "anthropic/claude-sonnet-4.5",
        )

    def _create_client(self) -> Any:
        """Create OpenAI SDK client pointed at OpenRouter."""
        if OpenAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "openai package is not installed. Install optional dependencies to use OpenRouterProvider."
            )
        kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "base_url": self.base_url or self.DEFAULT_BASE_URL,
        }
        # Optional ranking/attribution headers honored by OpenRouter.
        import os
        default_headers: dict[str, str] = {}
        referer = os.environ.get("OPENROUTER_HTTP_REFERER")
        title = os.environ.get("OPENROUTER_X_TITLE")
        if referer:
            default_headers["HTTP-Referer"] = referer
        if title:
            default_headers["X-Title"] = title
        if default_headers:
            kwargs["default_headers"] = default_headers

        if os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
            import httpx
            kwargs["http_client"] = httpx.Client(verify=False)
        return OpenAI(**kwargs)

    def get_available_models(self) -> list[str]:
        """Return a curated list of popular OpenRouter model IDs.

        OpenRouter supports hundreds of models; this list is a starting point —
        any valid ``vendor/model`` ID accepted by OpenRouter can be used.
        """
        return [
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
        ]
