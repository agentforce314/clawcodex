"""Production ``AgentRunner`` — the bridge from ``agent()`` to a real subagent.

This is the integration seam. The engine core (sandbox / scheduler / budget /
journal / primitives) is fully unit-tested against the in-memory ``FakeRunner``;
``LiveAgentRunner`` wires the same ``AgentRunner`` protocol to the real
``src.agent.run_agent`` loop, ``finalize_agent_tool`` (final text + token usage),
and the schema-validated ``StructuredOutput`` tool from
:mod:`src.workflow.structured`.

Its app-specific dependencies (provider, tool registry, parent context, the
base worker tool pool, and agent-type resolution) are injected by the caller —
in production, the Workflow tool builds them from its ``ToolContext``. The
structured-output path here is exercised by the ``make_structured_output_tool``
unit tests; the ``run_agent`` composition is validated by live integration
testing (it needs a real provider) and is intentionally thin.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from src.utils.abort_controller import AbortController, AbortError

from .structured import (
    SYNTHETIC_OUTPUT_TOOL_NAME,
    StructuredOutputCollector,
    make_structured_output_tool,
)
from .types import AgentOutcome, AgentSpec

#: Appended to a schema call's prompt so the model emits via the injected tool.
_SCHEMA_NUDGE = (
    "\n\nWhen you are finished, call the StructuredOutput tool exactly once with "
    "your final answer as its arguments. Do not put the answer anywhere else."
)


class LiveAgentRunner:
    def __init__(
        self,
        *,
        provider: Any,
        tool_registry: Any,
        parent_context: Any,
        base_tools: list,
        resolve_agent: Callable[[str], Any],
        default_agent_type: str = "general-purpose",
        run_id: str = "wf",
        max_turns: Optional[int] = None,
    ) -> None:
        self._provider = provider
        self._tool_registry = tool_registry
        self._parent_context = parent_context
        self._base_tools = list(base_tools)
        self._resolve_agent = resolve_agent
        self._default_agent_type = default_agent_type
        self._run_id = run_id
        self._max_turns = max_turns

    async def run(self, spec: AgentSpec, *, abort: AbortController, index: int) -> AgentOutcome:
        # Imported lazily: ``src.agent`` pulls in the whole agent stack, which
        # the engine core deliberately never imports.
        from src.agent.agent_tool_utils import finalize_agent_tool
        from src.agent.run_agent import RunAgentParams, run_agent

        agent_type = spec.agent_type or self._default_agent_type
        agent_definition = self._resolve_agent(agent_type)
        agent_id = f"wf_{self._run_id}-{index}"

        # isolation="worktree" is not yet wired (run_agent worktree support is a
        # later phase); a worktree request currently runs in-place.
        collector: Optional[StructuredOutputCollector] = None
        available_tools = list(self._base_tools)
        prompt = spec.prompt
        if spec.schema is not None:
            collector = StructuredOutputCollector(schema=spec.schema)
            available_tools = [
                t for t in available_tools if getattr(t, "name", None) != SYNTHETIC_OUTPUT_TOOL_NAME
            ]
            available_tools.append(make_structured_output_tool(collector))
            prompt = spec.prompt + _SCHEMA_NUDGE

        params = RunAgentParams(
            parent_context=self._parent_context,
            agent_definition=agent_definition,
            prompt=prompt,
            available_tools=available_tools,
            tool_registry=self._tool_registry,
            provider=self._provider,
            model=spec.model,
            agent_id=agent_id,
            abort_controller=abort,
            max_turns=self._max_turns,
            # Keep the injected StructuredOutput tool in the pool verbatim.
            use_exact_tools=spec.schema is not None,
        )

        messages: list = []
        try:
            async for message in run_agent(params):
                messages.append(message)
        except AbortError:
            raise  # cancellation unwinds; the engine marks the agent aborted

        # finalize_agent_tool raises if the run produced no assistant message;
        # the engine catches that and resolves agent() to None (a "death").
        result = finalize_agent_tool(messages, agent_id, {"agent_type": agent_type})
        tokens = result.total_tokens
        tool_uses = result.total_tool_use_count

        if spec.schema is not None:
            assert collector is not None
            if collector.succeeded:
                return AgentOutcome(structured=collector.value, tokens=tokens, tool_use_count=tool_uses)
            return AgentOutcome(
                error=f"structured output not produced (last error: {collector.last_error})",
                tokens=tokens,
                tool_use_count=tool_uses,
            )

        text = "".join(block.get("text", "") for block in result.content)
        return AgentOutcome(text=text, tokens=tokens, tool_use_count=tool_uses)
