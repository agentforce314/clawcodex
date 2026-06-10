"""Core data types and the subagent-runner seam for the workflow engine.

``AgentRunner`` is the dependency-injection boundary: in production it wraps
``src.agent.run_agent`` + ``finalize_agent_tool`` + schema-validated
structured output (see ``src/workflow/runner.py``); in tests it is a fake
that returns canned ``AgentOutcome``s. The engine itself never imports the
agent stack, which keeps the orchestration logic unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol

from src.utils.abort_controller import AbortController


@dataclass(frozen=True)
class WorkflowMeta:
    """The validated ``meta`` block extracted statically from a script."""

    name: str
    description: str
    when_to_use: Optional[str] = None
    phases: list[dict[str, Any]] = field(default_factory=list)
    model: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentSpec:
    """A single ``agent(prompt, **opts)`` request from the script."""

    prompt: str
    label: Optional[str] = None
    phase: Optional[str] = None
    schema: Optional[Mapping[str, Any]] = None
    model: Optional[str] = None
    agent_type: Optional[str] = None
    isolation: Optional[str] = None


@dataclass
class AgentOutcome:
    """The result of running one subagent.

    For a plain call the engine returns ``text``; for a ``schema`` call it
    returns ``structured``. ``skipped``/``error`` cause ``agent()`` to
    resolve to ``None``. ``tokens`` feeds the run-wide ``budget``.
    """

    text: Optional[str] = None
    structured: Any = None
    tokens: int = 0
    tool_use_count: int = 0
    error: Optional[str] = None
    skipped: bool = False


class AgentRunner(Protocol):
    """The subagent-spawning seam the workflow ``agent()`` primitive calls."""

    async def run(
        self,
        spec: AgentSpec,
        *,
        abort: AbortController,
        index: str,
    ) -> AgentOutcome:
        """``index`` is the call's deterministic call-path key as a string
        (e.g. ``"0"``, ``"1.0"``) — stable across runs, suitable as an
        agent id suffix."""
        ...
