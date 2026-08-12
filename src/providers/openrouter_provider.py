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

    provider_id = "openrouter"

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
        """Curated popular OpenRouter model IDs ∪ the endpoint's live list.

        OpenRouter's hosted catalog churns weekly (INTEG-1): the live list is
        discovered via the non-blocking TTL cache and merged over this
        curated starting point; any valid ``vendor/model`` ID is accepted
        regardless.
        """
        from src.providers.model_discovery import discovered_models

        return discovered_models(
            "openrouter",
            getattr(self, "base_url", None) or "https://openrouter.ai/api/v1",
            getattr(self, "api_key", None) or None,
            "openai-compatible",
            self._curated_models(),
            mode="hybrid",  # gateways/openrouter.ts source:'hybrid' — curation first
        )

    @staticmethod
    def _curated_models() -> list[str]:
        """The curated catalogue, read from the one place it is defined.

        This used to be a byte-identical second copy of
        ``PROVIDER_INFO["openrouter"]["available_models"]``. Two lists meant
        for the same set drift the moment someone updates one of them, and the
        halves are read by different surfaces — the registry entry feeds
        ``login`` and the /model picker, this feeds discovery's curation — so
        the drift shows up as a model the picker offers and discovery drops,
        or the reverse.

        Imported inside the function, not at module scope: this module is
        itself imported lazily by ``get_provider_class``, so a top-level import
        of the package that defines that function would be circular.
        """
        from . import PROVIDER_INFO

        return list(PROVIDER_INFO["openrouter"]["available_models"])
