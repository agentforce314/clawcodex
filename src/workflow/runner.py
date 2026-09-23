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

import hashlib
import json
import logging
import time
from typing import Any, Callable, Optional

from src.utils.abort_controller import AbortController, AbortError

from .structured import (
    SYNTHETIC_OUTPUT_TOOL_NAME,
    StructuredOutputCollector,
    make_structured_output_tool,
)
from .types import AgentOutcome, AgentSpec

logger = logging.getLogger(__name__)

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


# Parent modes that already auto-approve at least as much as acceptEdits — never
# downgrade these for a workflow subagent (matches resolve_permission_mode's
# "permissive parent takes precedence" rule in src/agent/run_agent.py).
_PERMISSIVE_PARENT_MODES = ("bypassPermissions", "acceptEdits", "dontAsk")


def _subagent_permission_override(parent_context: Any) -> Optional[str]:
    """Permission mode to force on a workflow subagent, or ``None`` to inherit.

    Forces ``acceptEdits`` (auto-approve file edits) for restrictive sessions
    (default/plan), but returns ``None`` for an already-permissive session so
    ``resolve_permission_mode`` carries the parent mode through — crucially,
    ``bypassPermissions`` from ``--dangerously-skip-permissions`` is inherited
    rather than silently downgraded to the more-restrictive ``acceptEdits``.
    """
    mode = getattr(getattr(parent_context, "permission_context", None), "mode", None)
    if mode in _PERMISSIVE_PARENT_MODES:
        return None
    return "acceptEdits"


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
        self._worktrees: dict[str, Any] = {}

    def _agent_id(self, index: str) -> str:
        # Stable for an agent's schema-repair attempts, safe for transcript paths.
        digest = hashlib.sha256(f"{self._run_id}:{index}".encode()).hexdigest()[:20]
        return f"a{digest}"

    async def run(self, spec: AgentSpec, *, abort: AbortController, index: str) -> AgentOutcome:
        context = self._parent_context
        agent_id = self._agent_id(index)
        tracking = context.query_tracking
        depth = tracking.depth + 1 if tracking is not None else 0
        context.agent_supervisor.admit(
            subagent_id=agent_id,
            parent_id=context.agent_id,
            depth=depth,
            goal=spec.label or spec.prompt[:80],
            model=spec.model,
            abort_controller=abort,
        )
        status = "failed"
        usage = AgentOutcome()
        try:
            outcome = await self._run_with_isolation(
                spec, abort=abort, index=index, usage=usage
            )
            status = "failed" if outcome.error else "completed"
            return outcome
        except Exception as exc:
            return AgentOutcome(
                tokens=usage.tokens,
                tool_use_count=usage.tool_use_count,
                skipped=abort.signal.aborted,
                error=None if abort.signal.aborted else f"{type(exc).__name__}: {exc}",
                worktree_path=usage.worktree_path,
            )
        finally:
            if abort.signal.aborted:
                status = "interrupted"
            emit = context.agent_progress_emit
            if emit is not None:
                try:
                    emit(
                        {
                            "agent_id": agent_id,
                            "depth": depth,
                            "name": spec.label,
                            "description": spec.prompt[:80],
                            "subagent_type": spec.agent_type
                            or self._default_agent_type,
                            "status": status,
                            "tool_use_id": context.tool_use_id,
                        }
                    )
                except Exception:
                    logger.debug("workflow progress emit failed", exc_info=True)
            context.agent_supervisor.release(agent_id)

    async def _run_with_isolation(
        self,
        spec: AgentSpec,
        *,
        abort: AbortController,
        index: str,
        usage: AgentOutcome,
    ) -> AgentOutcome:
        if spec.isolation == "worktree":
            import asyncio
            import dataclasses

            from src.agent.worktree import AgentWorktree
            from src.workflow.worktree import worktree_slug

            base_cwd = str(
                self._parent_context.cwd or self._parent_context.workspace_root
            )
            wt = self._worktrees.get(index)
            if wt is None or not wt.path.exists():
                wt = await asyncio.to_thread(
                    AgentWorktree.create, base_cwd, worktree_slug(self._run_id, index)
                )
                self._worktrees[index] = wt
            assert wt is not None
            wt.closed = False
            wt.in_use = (
                lambda: self._parent_context.agent_supervisor.has_live_descendants(
                    self._agent_id(index)
                )
            )
            context = dataclasses.replace(
                self._parent_context,
                cwd=wt.cwd,
                workspace_root=wt.path,
                worktree_root=wt.path,
            )
            try:
                outcome = await self._run_in_context(
                    spec, context, abort=abort, index=index, usage=usage
                )
            finally:
                await asyncio.to_thread(wt.close)
                if wt.retained:
                    usage.worktree_path = str(wt.path)
            if wt.retained:
                outcome.worktree_path = str(wt.path)
                if spec.schema is None:
                    outcome.text = (outcome.text or "") + "\n\n" + wt.notice()
            return outcome
        if spec.isolation is not None:
            raise ValueError(f"Unsupported agent isolation: {spec.isolation}")
        return await self._run_in_context(
            spec, self._parent_context, abort=abort, index=index, usage=usage
        )

    async def _run_in_context(
        self,
        spec: AgentSpec,
        parent_context: Any,
        *,
        abort: AbortController,
        index: str,
        usage: AgentOutcome,
    ) -> AgentOutcome:
        # Imported lazily: ``src.agent`` pulls in the whole agent stack, which
        # the engine core deliberately never imports.
        from src.agent.agent_tool_utils import finalize_agent_tool, resolve_agent_tools
        from src.agent.constants import ALL_AGENT_DISALLOWED_TOOLS, WORKFLOW_TOOL_NAME
        from src.agent.run_agent import RunAgentParams, run_agent
        from src.tasks.progress import (
            ProgressTracker,
            total_tokens_from_tracker,
            update_progress_from_message,
        )
        from src.tool_system.registry import ToolRegistry
        from src.types.messages import AssistantMessage, UserMessage

        agent_type = spec.agent_type or self._default_agent_type
        agent_definition = self._resolve_agent(agent_type)
        agent_id = self._agent_id(index)

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
                # The Workflow tool's contract says an agent() call without
                # opts.model "inherits the main-loop model", so default to
                # 'inherit' rather than passing None through: None now
                # resolves to the provider's cheap default subagent model,
                # which both breaks that contract and hands flash-class
                # models the deep-research WebSearch/WebFetch loop that
                # tools/workflow.py documents as a ~30x token burner on
                # exactly such models. The agent definition's own ``model:``
                # sits BETWEEN those (critic r4): this value fills the
                # tool-param slot, which outranks the agent-def slot in
                # get_agent_model — a bare 'inherit' here would silently
                # discard an opts.agentType agent's declared model (e.g.
                # Explore's 'haiku').
                model=spec.model or agent_definition.model or "inherit",
                agent_id=agent_id,
                abort_controller=abort,
                max_turns=self._max_turns,
                # Workflow subagents auto-approve file edits (acceptEdits) regardless
                # of the session's mode — per the spec — but NEVER downgrade an
                # already-permissive session. If the user launched with
                # --dangerously-skip-permissions (bypassPermissions), or any other
                # permissive parent mode, inherit it (override=None lets
                # resolve_permission_mode carry the parent mode through); otherwise
                # elevate restrictive modes (default/plan) to acceptEdits.
                permission_mode_override=_subagent_permission_override(parent_context),
                # Already resolved + firewalled + injected; don't let run_agent
                # re-resolve (which would drop the injected StructuredOutput tool).
                use_exact_tools=True,
            )

            from src.agent.transcript import TranscriptWriter, get_agent_transcript_path

            tracker = ProgressTracker()
            messages: list = []
            transcript = None
            started = time.time()
            try:
                transcript = TranscriptWriter(get_agent_transcript_path(agent_id))
                transcript.append(UserMessage(content=prompt_text))
            except OSError:
                logger.debug("workflow transcript unavailable", exc_info=True)
            try:
                async for message in run_agent(params):
                    messages.append(message)
                    update_progress_from_message(tracker, message)
                    if transcript is not None:
                        try:
                            transcript.append(message)
                        except OSError:
                            transcript.close()
                            transcript = None
                    parent_context.agent_supervisor.set_tool_count(
                        agent_id, tracker.tool_use_count
                    )
                    emit = parent_context.agent_progress_emit
                    if emit is not None:
                        try:
                            tracking = parent_context.query_tracking
                            emit(
                                {
                                    "agent_id": agent_id,
                                    "depth": tracking.depth + 1 if tracking else 0,
                                    "name": spec.label,
                                    "description": spec.prompt[:80],
                                    "subagent_type": agent_type,
                                    "status": "running",
                                    "tool_use_count": tracker.tool_use_count,
                                    "tool_use_id": parent_context.tool_use_id,
                                }
                            )
                        except Exception:
                            logger.debug("workflow progress emit failed", exc_info=True)
            finally:
                usage.tokens += total_tokens_from_tracker(tracker)
                usage.tool_use_count += tracker.tool_use_count
                if transcript is not None:
                    transcript.close()
            abort.signal.throw_if_aborted()
            result = finalize_agent_tool(
                messages,
                agent_id,
                {"agent_type": agent_type, "start_time": started},
                progress=tracker,
            )
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
