"""AgentLoop Protocol — interface for multi-turn agent execution.

Phase 1: Stub with NotImplementedError.
This Protocol defines the contract for agent loop implementations.
Concrete implementation is in src/tool_system/agent_loop.py.

See: docs/UPSTREAM_SYNC_DESIGN-decoupling.md Section 6 (Agent 集成)
"""

from typing import Protocol

__all__ = ["AgentLoopProtocol"]


class AgentLoopProtocol(Protocol):
    """Protocol for multi-turn tool-calling agent loops.

    Implementors must provide:
      - run_turn(provider, messages, tools) -> AgentResult
      - summarize_result(result) -> str
      - is_anthropic_provider(provider) -> bool
    """

    def run_turn(
        self,
        provider: "BaseProvider",  # noqa: F821
        messages: "list[Message]",  # noqa: F821
        tools: "list[Tool]",  # noqa: F821
    ) -> "AgentResult": ...  # pragma: no cover

    def summarize_result(self, result: "AgentResult") -> str: ...  # pragma: no cover

    def is_anthropic_provider(self, provider: "BaseProvider") -> bool: ...  # pragma: no cover