"""AgentLoop Protocol — interface for multi-turn agent execution.

This Protocol defines the contract for agent loop implementations.
Concrete implementation is in src/tool_system/agent_loop.py.

See: docs/UPSTREAM_SYNC_DESIGN-decoupling.md Section 6 (Agent 集成)
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["AgentLoopProtocol"]


class AgentLoopProtocol(Protocol):
    """Protocol for multi-turn tool-calling agent loops.

    Implementors must provide:
      - run_agent_loop(...) -> AgentLoopResult
      - summarize_tool_result(name, output) -> str
      - summarize_tool_use(name, tool_input) -> str
      - is_anthropic_provider(provider) -> bool
    """

    def run_agent_loop(
        self,
        conversation: "Conversation",  # noqa: F821
        provider: "BaseProvider",  # noqa: F821
        tool_registry: "ToolRegistry",  # noqa: F821
        tool_context: "ToolContext",  # noqa: F821
        max_turns: int = 20,
        stream: bool = False,
        verbose: bool = False,
        on_event: "ToolEventHandler | None" = None,  # noqa: F821
        on_text_chunk: "TextChunkHandler | None" = None,  # noqa: F821
        cancel_signal: "AbortSignal | None" = None,  # noqa: F821
    ) -> "AgentLoopResult": ...  # pragma: no cover

    def summarize_tool_result(self, name: str, output: object) -> str: ...  # pragma: no cover

    def summarize_tool_use(self, name: str, tool_input: dict[str, object]) -> str: ...  # pragma: no cover

    def is_anthropic_provider(self, provider: "BaseProvider") -> bool: ...  # pragma: no cover