"""Base provider abstract class for LLM providers."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Generator, Optional, TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from src.utils.abort_controller import AbortSignal


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
    # Raw content blocks the provider chose not to project into
    # ``content``/``tool_uses`` — currently the server-side advisor's
    # ``server_tool_use`` (name=advisor) and ``advisor_tool_result``
    # blocks. The query loop appends these to assistant history as
    # opaque passthrough dicts so the next turn can replay them. None
    # when the response had no such blocks. See
    # ``src/utils/advisor.py`` for the policy.
    raw_content_blocks: Optional[list[dict[str, Any]]] = None


MessageInput: TypeAlias = ChatMessage | dict[str, Any]
TextChunkCallback: TypeAlias = Callable[[str], None]


class BaseProvider(ABC):
    """Base class for LLM providers."""

    #: Whether this provider talks to DeepSeek's API. Overridden to ``True``
    #: in :class:`~src.providers.deepseek_provider.DeepSeekProvider`. Gates
    #: DeepSeek-only token-efficiency behaviour (prompt-prefix-cache
    #: stability) without affecting any other provider. Scope is the
    #: ``deepseek`` provider class ONLY — a DeepSeek model served via
    #: OpenRouter is intentionally NOT covered.
    is_deepseek: bool = False

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

    async def chat_async(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> ChatResponse:
        """Asynchronous chat completion.

        Default implementation offloads the synchronous :meth:`chat` to a
        worker thread via :func:`asyncio.to_thread`, giving async callers —
        full/partial compaction (``services/compact/compact.py``), agent
        hooks (``hooks/exec_agent_hook.py``), and the memdir selector
        (``memdir/find_relevant_memories.py``) — a non-blocking entry point
        without every provider needing a native async SDK client. The
        providers wrap blocking HTTP SDKs (``openai`` / ``anthropic``), so
        running ``chat`` inline on the event loop would stall concurrent
        work (and, in compaction's case, the whole turn); the thread offload
        keeps the loop responsive. Providers backed by an async client may
        override this with a true coroutine implementation.

        Accepts the same positional ``messages`` / ``tools`` and ``**kwargs``
        (``model``, ``max_tokens``, ``system``, …) as :meth:`chat`.
        """
        return await asyncio.to_thread(self.chat, messages, tools, **kwargs)

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
        abort_signal: "AbortSignal | None" = None,
        **kwargs
    ) -> ChatResponse:
        """Stream a response while also returning the final structured ChatResponse.

        When ``abort_signal`` is provided, a provider implementation should
        register a listener on it that forcibly closes the underlying HTTP
        stream when the signal fires. Without this, a tripped abort can only
        be observed between chunks via ``on_text_chunk`` — which never fires
        for a turn that emits tool_use blocks without intervening text, so
        ESC ends up waiting for the model to finish generating before the
        outer query loop can bail.

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
            Model name to use. The optional ``[1m]`` opt-in suffix
            (WI-5.3) is stripped here so the API receives a clean
            model id; the suffix's only effect is driving
            ``get_context_window_for_model`` to return 1M tokens.
        """
        # Late import to avoid a top-level dependency from base.py
        # into the models package.
        from src.models.context import strip_1m_context_suffix
        raw = kwargs.get("model", self.model)
        return strip_1m_context_suffix(raw) if raw else raw

    def _prepare_messages(self, messages: list[MessageInput]) -> list[dict[str, Any]]:
        """Convert provider messages to API dictionary format.

        Also runs ``validate_images_for_api`` so every provider — not just
        the Anthropic-direct ``services/api/claude.py:call_model`` path —
        rejects oversize base64 images before the network round trip.
        Anthropic's 5 MB hard limit applies to its own provider; for
        OpenAI-compatible providers the check runs on the still-Anthropic
        shape (subclasses call ``super()._prepare_messages`` before
        translating to ``image_url``), so the same client-side guard
        applies. Providers without image support are unaffected: the
        walker only inspects ``type=image`` blocks. Raises
        ``ImageSizeError``; the caller (``query._call_model_sync``)
        translates it into a media-size error message rather than
        letting it surface as an opaque API failure.
        """
        prepared = [msg if isinstance(msg, dict) else msg.to_dict() for msg in messages]
        # Local import to avoid a top-level dependency from base.py into
        # the utils package, matching the style of ``_get_model``.
        from src.utils.image_validation import validate_images_for_api
        validate_images_for_api(prepared)
        return prepared
