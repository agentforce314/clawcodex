"""
LiteLLM adapter for ClawCodex LLM Provider layer.

This module provides a LiteLLM-based unified provider that can replace
the individual provider implementations in src/providers/.

Architecture:
    src/providers/base.py (BaseProvider abstract class)
        ↓
    src/providers/_litellm_adapter.py (This module - LiteLLM backend)
        ↓
    LiteLLM (Open source dependency)

Switch:
    CLAW_USE_LITELLM=false (default) - use original provider implementations
    CLAW_USE_LITELLM=true - use LiteLLM unified provider
"""

from __future__ import annotations

import logging
import os
from typing import Any, Generator, Optional, TYPE_CHECKING

from .base import BaseProvider, ChatMessage, ChatResponse, MessageInput

logger = logging.getLogger(__name__)

# Switching mechanism: control via environment variable
_USE_LITELLM = os.getenv("CLAW_USE_LITELLM", "false").lower() in ("true", "1")

# LiteLLM availability
try:
    import litellm
    from litellm import acompletion, token_counter
    _LITELLM_AVAILABLE = True
except ImportError:
    _LITELLM_AVAILABLE = False
    litellm = None
    acompletion = None
    token_counter = None


def is_litellm_available() -> bool:
    """Check if LiteLLM is available."""
    return _LITELLM_AVAILABLE


class LiteLLMProvider(BaseProvider):
    """
    Unified LLM Provider using LiteLLM.

    This provider wraps LiteLLM's completion API to provide a unified
    interface for multiple LLM providers (OpenAI, Anthropic, Gemini, etc.)
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider_name: str = "openai",
    ):
        """
        Initialize LiteLLM provider.

        Args:
            api_key: API key for authentication
            base_url: Base URL for API endpoint
            model: Default model to use
            provider_name: The provider name (e.g., "anthropic", "openai", "gemini")
        """
        super().__init__(api_key=api_key, base_url=base_url, model=model)
        self.provider_name = provider_name

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> ChatResponse:
        """Synchronous chat completion via LiteLLM."""
        if not _LITELLM_AVAILABLE:
            raise RuntimeError("LiteLLM is not installed")

        prepared = self._prepare_messages(messages)
        model = self._get_model(**kwargs) or self.model
        litellm_model = f"{self.provider_name}/{model}" if self.provider_name else model

        response = litellm.completion(
            model=litellm_model,
            messages=prepared,
            tools=tools,
            api_key=self.api_key,
            base_url=self.base_url,
            **kwargs
        )

        return self._convert_response(response)

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        """Streaming chat completion via LiteLLM."""
        if not _LITELLM_AVAILABLE:
            raise RuntimeError("LiteLLM is not installed")

        prepared = self._prepare_messages(messages)
        model = self._get_model(**kwargs) or self.model
        litellm_model = f"{self.provider_name}/{model}" if self.provider_name else model

        response = litellm.completion(
            model=litellm_model,
            messages=prepared,
            tools=tools,
            api_key=self.api_key,
            base_url=self.base_url,
            stream=True,
            **kwargs
        )

        for chunk in response:
            content = self._extract_chunk_content(chunk)
            if content:
                yield content

    def _extract_chunk_content(self, chunk: dict[str, Any]) -> str:
        """Extract content from a streaming chunk."""
        try:
            choices = chunk.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                return delta.get("content", "")
        except Exception:
            pass
        return ""

    def _convert_response(self, response: dict[str, Any]) -> ChatResponse:
        """Convert LiteLLM response to ChatResponse."""
        try:
            choice = response["choices"][0]
            message = choice.get("message", {})

            return ChatResponse(
                content=message.get("content", ""),
                model=response.get("model", self.model or ""),
                usage=response.get("usage", {}),
                finish_reason=choice.get("finish_reason", ""),
                reasoning_content=message.get("reasoning_content", None),
                tool_uses=message.get("tool_calls", None),
            )
        except (KeyError, IndexError) as e:
            logger.error("Failed to convert LiteLLM response: %s", e)
            return ChatResponse(
                content="",
                model=self.model or "",
                usage={},
                finish_reason="error",
            )

    def get_available_models(self) -> list[str]:
        """Get list of available models from LiteLLM."""
        if not _LITELLM_AVAILABLE:
            return []
        try:
            return litellm.model_list
        except Exception:
            return []

    def count_tokens(self, text: str) -> int:
        """Count tokens for a given text using LiteLLM."""
        if not _LITELLM_AVAILABLE:
            return 0
        try:
            return token_counter(model=self.model, text=text)
        except Exception:
            return 0


def create_litellm_provider(
    provider_name: str,
    api_key: str,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> LiteLLMProvider:
    """
    Factory function to create a LiteLLM provider for a specific provider.

    Args:
        provider_name: The provider name (e.g., "anthropic", "openai", "gemini")
        api_key: API key
        base_url: Optional base URL override
        model: Optional model name

    Returns:
        A configured LiteLLMProvider instance
    """
    return LiteLLMProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider_name=provider_name,
    )