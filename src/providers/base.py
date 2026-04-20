"""Base provider abstract class for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Generator, Optional, TypeAlias


@dataclass
class ChatMessage:
    """Represents a chat message."""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary."""
        return {"role": self.role, "content": self.content}


@dataclass
class ChatResponse:
    """Represents a chat response."""

    content: str
    model: str
    usage: dict[str, Any]
    finish_reason: str
    reasoning_content: Optional[str] = None
    tool_uses: Optional[list[dict[str, Any]]] = None


MessageInput: TypeAlias = ChatMessage | dict[str, Any]
TextChunkCallback: TypeAlias = Callable[[str], None]


class BaseProvider(ABC):
    """Base class for LLM providers."""

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        """Initialize provider.

        Args:
            api_key: API key for authentication
            base_url: Base URL for API endpoint
            model: Default model to use
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    @abstractmethod
    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> ChatResponse:
        """Synchronous chat completion.

        Args:
            messages: List of chat messages
            tools: Optional list of tool schemas
            **kwargs: Additional provider-specific parameters

        Returns:
            Chat response
        """
        pass

    @abstractmethod
    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        """Streaming chat completion.

        Args:
            messages: List of chat messages
            tools: Optional list of tool schemas
            **kwargs: Additional provider-specific parameters

        Yields:
            Chunks of response content
        """
        pass

    def chat_stream_response(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        on_text_chunk: TextChunkCallback | None = None,
        **kwargs
    ) -> ChatResponse:
        """Stream a response while also returning the final structured ChatResponse.

        Providers may override this to support tool-aware streaming. The default
        implementation signals that rich streamed responses are unavailable.
        """
        raise NotImplementedError("Structured streaming is not supported by this provider")

    @abstractmethod
    def get_available_models(self) -> list[str]:
        """Get list of available models.

        Returns:
            List of model names
        """
        pass

    def _get_model(self, **kwargs) -> str:
        """Get model from kwargs or use default.

        Args:
            **kwargs: Keyword arguments that may contain 'model'

        Returns:
            Model name to use
        """
        return kwargs.get("model", self.model)

    def _prepare_messages(self, messages: list[MessageInput]) -> list[dict[str, Any]]:
        """Convert provider messages to API dictionary format."""
        return [msg if isinstance(msg, dict) else msg.to_dict() for msg in messages]
