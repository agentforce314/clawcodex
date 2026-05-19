"""ContextBuilder Protocol — interface for context/prompt construction.

Phase 1: Stub with NotImplementedError.
This Protocol defines the contract for building execution context.
Concrete implementation is in src/context_system/__init__.py (build_context_prompt).

See: src/tool_system/agent_loop.py imports from context_system
"""

from typing import Protocol

__all__ = ["ContextBuilderProtocol"]


class ContextBuilderProtocol(Protocol):
    """Protocol for building tool execution context.

    Implementors must provide:
      - build_context_prompt(...) -> str
    """

    def build_context_prompt(self, *args, **kwargs) -> str: ...  # pragma: no cover