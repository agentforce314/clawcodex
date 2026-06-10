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

    async def run(self, spec: AgentSpec, *, abort: AbortController, index: str) -> AgentOutcome:
        # isolation="worktree": run the agent in a throwaway git worktree so
        # parallel file-mutating agents don't collide. Best-effort — if the
        # worktree can't be created the agent runs in place.
        if spec.isolation == "worktree":
            import dataclasses
            from pathlib import Path as _Path

            from src.workflow.worktree import agent_worktree

            base_cwd = str(self._parent_context.cwd) if getattr(self._parent_context, "cwd", None) else "."
            async with agent_worktree(self._run_id, index, base_cwd) as wt:
                context = (
                    dataclasses.replace(self._parent_context, cwd=_Path(wt))
                    if wt
                    else self._parent_context
                )
                return await self._run_in_context(spec, context, abort=abort, index=index)
        return await self._run_in_context(spec, self._parent_context, abort=abort, index=index)

    async def _run_in_context(
        self, spec: AgentSpec, parent_context: Any, *, abort: AbortController, index: str
    ) -> AgentOutcome:
        # Imported lazily: ``src.agent`` pulls in the whole agent stack, which
        # the engine core deliberately never imports.
        from src.agent.agent_tool_utils import finalize_agent_tool, resolve_agent_tools
        from src.agent.constants import ALL_AGENT_DISALLOWED_TOOLS, WORKFLOW_TOOL_NAME
        from src.agent.run_agent import RunAgentParams, run_agent
        from src.tasks.progress import ProgressTracker, update_progress_from_message
        from src.tool_system.registry import ToolRegistry
        from src.types.messages import AssistantMessage

        agent_type = spec.agent_type or self._default_agent_type
        agent_definition = self._resolve_agent(agent_type)
        agent_id = f"wf_{self._run_id}-{index}"

        # Resolve the agent's *scoped, firewalled* toolset (applies
        # ALL_AGENT_DISALLOWED_TOOLS — including Workflow, so a subagent can't
        # recurse into another workflow — plus the agent definition's own tool
        # scoping). We then pass it with use_exact_tools=True so run_agent keeps
        # the injected StructuredOutput tool verbatim instead of re-resolving and
        # dropping it. Belt-and-braces: strip Workflow even if it slips through.
        resolved = resolve_agent_tools(agent_definition, self._base_tools, is_async=False)
        worker_tools = [t for t in resolved.resolved_tools if getattr(t, "name", "") != WORKFLOW_TOOL_NAME]

        collector: Optional[StructuredOutputCollector] = None
        prompt = spec.prompt
        structured_tool = None
        if spec.schema is not None:
            collector = StructuredOutputCollector(schema=spec.schema)
            structured_tool = make_structured_output_tool(collector)
            worker_tools = [t for t in worker_tools if getattr(t, "name", "") != SYNTHETIC_OUTPUT_TOOL_NAME]
            worker_tools.append(structured_tool)
            prompt = spec.prompt + _SCHEMA_NUDGE
        available_tools = worker_tools

        # Tool DISPATCH resolves by name from the registry, not from
        # ``available_tools`` — so the schema agent needs a per-call registry in
        # which ``StructuredOutput`` is *our* validating tool (not the stock
        # no-op) and ``Workflow`` is absent. Built per call so concurrent schema
        # agents don't share a collector.
        agent_registry = ToolRegistry()
        for t in self._tool_registry.list_tools():
            # Same firewall as the advertised pool: no Agent/Workflow/TaskStop/...
            # so a subagent can't recurse or escalate via a by-name dispatch.
            if getattr(t, "name", "") in ALL_AGENT_DISALLOWED_TOOLS:
                continue
            if structured_tool is not None and getattr(t, "name", "") == SYNTHETIC_OUTPUT_TOOL_NAME:
                continue
            agent_registry.register(t)
        if structured_tool is not None:
            agent_registry.register(structured_tool)

        params = RunAgentParams(
            parent_context=parent_context,
            agent_definition=agent_definition,
            prompt=prompt,
            available_tools=available_tools,
            tool_registry=agent_registry,
            provider=self._provider,
            model=spec.model,
            agent_id=agent_id,
            abort_controller=abort,
            max_turns=self._max_turns,
            # Workflow subagents always run acceptEdits (file edits auto-approved)
            # regardless of the session's mode — per the official spec. run_agent
            # honors this override ahead of the inheritance rules.
            permission_mode_override="acceptEdits",
            # We already resolved + firewalled + injected; don't let run_agent
            # re-resolve (which would drop the injected StructuredOutput tool).
            use_exact_tools=True,
        )

        # Feed a ProgressTracker so finalize_agent_tool reports chapter-correct
        # token totals (latest input + cumulative output) rather than the
        # message.usage fallback — these tokens drive the workflow budget.
        tracker = ProgressTracker()
        messages: list = []
        try:
            async for message in run_agent(params):
                messages.append(message)
                if isinstance(message, AssistantMessage):
                    try:
                        update_progress_from_message(tracker, message)
                    except Exception:  # noqa: BLE001 — progress is best-effort
                        pass
        except AbortError:
            raise  # cancellation unwinds; the engine marks the agent aborted

        # finalize_agent_tool raises if the run produced no assistant message;
        # the engine catches that and resolves agent() to None (a "death").
        result = finalize_agent_tool(messages, agent_id, {"agent_type": agent_type}, progress=tracker)
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
