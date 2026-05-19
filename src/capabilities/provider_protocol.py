"""LLMProvider Protocol — interface for LLM provider abstraction.

Phase 1: Stub with NotImplementedError.
This Protocol defines the contract for LLM providers.
Concrete implementation is in src/providers/base.py (BaseProvider).

Note: src/providers/ is already in Layer 3 (features). This Protocol is
the abstraction boundary that Layer 1 (upstream) uses to talk to Layer 3.

See: src/tool_system/agent_loop.py imports from providers.base
"""

from typing import Protocol

__all__ = ["LLMProviderProtocol"]


class LLMProviderProtocol(Protocol):
    """Protocol for LLM provider abstraction.

    Implementors must provide:
      - chat(messages, tools, **kwargs) -> ChatResponse
      - stream(messages, tools, **kwargs) -> Iterator[ChatResponse]
    """

    def chat(
        self, messages: "list[Message]", tools: "list[Tool]", **kwargs  # noqa: F821
    ) -> "ChatResponse": ...  # pragma: no cover

    def stream(
        self, messages: "list[Message]", tools: "list[Tool]", **kwargs  # noqa: F821
    ) -> "Iterator[ChatResponse]": ...  # pragma: no cover