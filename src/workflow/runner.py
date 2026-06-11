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

import json
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


def _schema_repair_prompt(schema: Any, last_error: Optional[str]) -> str:
    """A corrective user turn appended to the SAME conversation on retry.

    The agent already has the data it gathered (search results, etc.) in context;
    this turn quotes the exact validation failure and asks it to re-emit via the
    tool — explicitly WITHOUT searching again. Cheap models that lapse into prose
    or the wrong shape on the first pass reliably correct here, since they only
    need to reformat data they already have.
    """
    reason = last_error or "you did not call the StructuredOutput tool at all"
    try:
        schema_json = json.dumps(schema, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — non-serializable schema: fall back to repr
        schema_json = str(schema)
    if len(schema_json) > 1500:
        schema_json = schema_json[:1500] + " …"
    return (
        "Your previous response did not produce valid structured output. "
        f"Reason: {reason}. "
        "You already have everything you need from the conversation above — do NOT "
        "search or fetch again. Now call the StructuredOutput tool exactly once, with "
        f"arguments that exactly match this JSON Schema:\n{schema_json}\n"
        "Every required field must be present with the correct type (an array must be a "
        "JSON array, not a string), add no extra fields, and put your entire answer in "
        "that single tool call."
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
        schema_max_attempts: int = 3,
    ) -> None:
        self._provider = provider
        self._tool_registry = tool_registry
        self._parent_context = parent_context
        self._base_tools = list(base_tools)
        self._resolve_agent = resolve_agent
        self._default_agent_type = default_agent_type
        self._run_id = run_id
        self._max_turns = max_turns
        # A schema agent that fails validation (or skips the tool) is re-run with
        # a corrective prompt, up to this many TOTAL attempts. Retries cost extra
        # only on failure — a model that gets it right first time pays nothing.
        self._schema_max_attempts = max(1, schema_max_attempts)

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
        from src.types.messages import AssistantMessage, UserMessage

        agent_type = spec.agent_type or self._default_agent_type
        agent_definition = self._resolve_agent(agent_type)
        agent_id = f"wf_{self._run_id}-{index}"

        # Resolve the agent's *scoped, firewalled* toolset (applies
        # ALL_AGENT_DISALLOWED_TOOLS — including Workflow, so a subagent can't
        # recurse into another workflow — plus the agent definition's own tool
        # scoping). use_exact_tools=True (below) keeps the injected StructuredOutput
        # tool verbatim. Belt-and-braces: strip Workflow even if it slips through.
        resolved = resolve_agent_tools(agent_definition, self._base_tools, is_async=False)
        base_worker_tools = [
            t for t in resolved.resolved_tools if getattr(t, "name", "") != WORKFLOW_TOOL_NAME
        ]

        def _name(t: Any) -> str:
            return getattr(t, "name", "")

        async def _attempt(prompt_text, collector, context_messages=None):
            """Run the agent once. Returns (result, tokens, tool_use_count, messages).

            ``context_messages`` carries the prior conversation on a retry so the
            agent keeps the data it already gathered. A fresh collector + registry
            per attempt avoids cross-attempt contamination of the validating tool.
            """
            structured_tool = make_structured_output_tool(collector) if collector is not None else None
            worker_tools = list(base_worker_tools)
            if structured_tool is not None:
                worker_tools = [t for t in worker_tools if _name(t) != SYNTHETIC_OUTPUT_TOOL_NAME]
                worker_tools.append(structured_tool)

            # Tool DISPATCH resolves by name from a per-call registry in which
            # StructuredOutput is *our* validating tool (not the stock no-op) and
            # the disallowed tools (Agent/Workflow/TaskStop/...) are absent — so a
            # subagent can't recurse or escalate via a by-name dispatch.
            agent_registry = ToolRegistry()
            for t in self._tool_registry.list_tools():
                if _name(t) in ALL_AGENT_DISALLOWED_TOOLS:
                    continue
                if structured_tool is not None and _name(t) == SYNTHETIC_OUTPUT_TOOL_NAME:
                    continue
                agent_registry.register(t)
            if structured_tool is not None:
                agent_registry.register(structured_tool)

            params = RunAgentParams(
                parent_context=parent_context,
                agent_definition=agent_definition,
                prompt=prompt_text,
                context_messages=context_messages,
                available_tools=worker_tools,
                tool_registry=agent_registry,
                provider=self._provider,
                model=spec.model,
                agent_id=agent_id,
                abort_controller=abort,
                max_turns=self._max_turns,
                # Workflow subagents always run acceptEdits (file edits auto-approved)
                # regardless of the session's mode — per the official spec.
                permission_mode_override="acceptEdits",
                # Already resolved + firewalled + injected; don't let run_agent
                # re-resolve (which would drop the injected StructuredOutput tool).
                use_exact_tools=True,
            )

            # ProgressTracker so finalize_agent_tool reports chapter-correct token
            # totals (latest input + cumulative output) — these drive the budget.
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
            return result, result.total_tokens, result.total_tool_use_count, messages

        # ── text agent: single shot ──────────────────────────────────────────
        if spec.schema is None:
            result, tokens, tool_uses, _ = await _attempt(spec.prompt, None)
            text = "".join(block.get("text", "") for block in result.content)
            return AgentOutcome(text=text, tokens=tokens, tool_use_count=tool_uses)

        # ── schema agent: emit-or-repair retry loop (context-preserving) ──────
        # Cheap/weak models often return the wrong shape (a string where an array
        # is required, a renamed/missing field) or skip the tool entirely — yet
        # reliably reformat correctly once told exactly what's wrong. So on a miss
        # we CONTINUE the same conversation (the agent keeps its gathered search
        # results) with a corrective turn, rather than re-running from scratch:
        # faster (no re-search) and higher-converging. Retries fire ONLY on
        # failure, so a model that nails it first time (e.g. opus) pays nothing
        # extra. Tokens accumulate across attempts to keep the budget accurate.
        total_tokens = 0
        total_tool_uses = 0
        last_error: Optional[str] = None
        convo: list = []
        attempts = self._schema_max_attempts
        for attempt in range(attempts):
            collector = StructuredOutputCollector(schema=spec.schema)
            if attempt == 0:
                prompt_text = spec.prompt + _SCHEMA_NUDGE
                context_messages = None
            else:
                prompt_text = _schema_repair_prompt(spec.schema, last_error)
                context_messages = convo
            result, tokens, tool_uses, produced = await _attempt(
                prompt_text, collector, context_messages
            )
            total_tokens += tokens
            total_tool_uses += tool_uses
            if collector.succeeded:
                return AgentOutcome(
                    structured=collector.value,
                    tokens=total_tokens,
                    tool_use_count=total_tool_uses,
                )
            last_error = collector.last_error
            # Carry the full conversation forward: prior context + this attempt's
            # user turn (run_agent appends ``prompt`` as a user message but does
            # not re-yield it) + the messages it produced.
            convo = (context_messages or []) + [UserMessage(content=prompt_text)] + produced

        return AgentOutcome(
            error=f"structured output not produced after {attempts} attempt(s) (last error: {last_error})",
            tokens=total_tokens,
            tool_use_count=total_tool_uses,
        )
