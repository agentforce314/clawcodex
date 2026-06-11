"""OpenAI provider implementation."""

from __future__ import annotations

from typing import Any, Optional

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from .openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI provider using OpenAI SDK."""

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        """Initialize OpenAI provider.

        Args:
            api_key: OpenAI API key
            base_url: Base URL (optional, for custom endpoints)
            model: Default model (default: gpt-5.4)
        """
        super().__init__(api_key, base_url, model or "gpt-5.4")

    def _create_client(self) -> Any:
        """Create OpenAI SDK client."""
        if OpenAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "openai package is not installed. Install optional dependencies to use OpenAIProvider."
            )
        import os

        import httpx

        # Bound how long a request may stall with NO bytes from the server.
        # Without this the SDK's sync streaming read blocks forever when an
        # endpoint accepts the request but never replies (observed with a
        # LiteLLM proxy on streaming + tool calls) — which freezes the asyncio
        # event loop the agent loop runs on, deadlocking every concurrent agent.
        # ``read`` is the max gap *between* bytes, so legitimate long streams are
        # fine as long as data keeps flowing. All tunable via env.
        read_timeout = float(os.environ.get("CLAWCODEX_LLM_READ_TIMEOUT", "120"))
        connect_timeout = float(os.environ.get("CLAWCODEX_LLM_CONNECT_TIMEOUT", "15"))
        timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=30.0, pool=15.0)
        max_retries = int(os.environ.get("CLAWCODEX_LLM_MAX_RETRIES", "1"))

        kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": timeout,
            "max_retries": max_retries,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        # Support SSL verification bypass for corporate/internal endpoints.
        if os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
            kwargs["http_client"] = httpx.Client(verify=False, timeout=timeout)
        return OpenAI(**kwargs)

    def get_available_models(self) -> list[str]:
        """Get list of available OpenAI models.

        Returns:
            List of model names
        """
        return [
            # GPT-5.4 series (latest flagship)
            "gpt-5.4",
            "gpt-5.4-pro",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            # GPT-5.2 series
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
        ]
