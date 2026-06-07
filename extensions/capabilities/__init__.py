"""capabilities — Layer 2: ClawCodex-specific Protocol definitions.

This package defines the interface contracts (Protocol classes) that form the
boundary between Layer 1 (upstream compat) and Layer 3 (features).

Design rules:
  - Use typing.Protocol for structural subtyping
  - No ABC inheritance (informal interfaces only)
  - No implementation — only method signatures with NotImplementedError
  - No imports from src.upstream (Layer 2 cannot depend on Layer 1)

Phase 1 status: stub Protocol files with NotImplementedError.
Actual implementation is Phase 2/3 work.

See: docs/UPSTREAM_SYNC_DESIGN-decoupling.md Section 4.2
"""

from .agent_protocol import AgentLoopProtocol
from .tool_protocol import ToolSystemProtocol
from .context_protocol import ContextBuilderProtocol
from .provider_protocol import LLMProviderProtocol
from .event_protocol import ToolEventProtocol
from .headless_protocol import HeadlessOptionsProtocol, HeadlessRunnerProtocol
from .headless_runner import HeadlessSessionOptions, run_headless_session
from .adapter_protocol import (  # noqa: F401
    AdapterInfo,
    AdapterProtocol,
    AdapterRegistry,
    dependency_available,
    env_switch,
    is_provider_adapter,
)

__all__ = [
    "AdapterInfo",
    "AdapterProtocol",
    "AdapterRegistry",
    "AgentLoopProtocol",
    "ContextBuilderProtocol",
    "HeadlessOptionsProtocol",
    "HeadlessRunnerProtocol",
    "HeadlessSessionOptions",
    "LLMProviderProtocol",
    "ToolEventProtocol",
    "ToolSystemProtocol",
    "dependency_available",
    "env_switch",
    "is_provider_adapter",
    "run_headless_session",
]