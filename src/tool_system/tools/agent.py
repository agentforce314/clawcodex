"""Agent tool — launches subagents with context isolation.

Mirrors typescript/src/tools/AgentTool/AgentTool.tsx.

Supports three modes:
1. **Sync child** — Parent waits for the agent to finish and returns the result.
2. **Async background** — Agent runs independently; parent gets an agent_id back
   immediately and can later query results via SendMessage.
3. **Fork** — Inherits parent context for prompt cache sharing (future).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any
from uuid import uuid4

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from ..registry import ToolRegistry

from src.agent.agent_definitions import (
    AgentDefinition,
    FORK_AGENT,
    find_agent_by_type,
    get_built_in_agents,
)
from src.agent.filter_agents_by_mcp import filter_agents_by_mcp_requirements
from src.agent.load_agents_dir import get_agent_definitions_with_overrides
from src.agent.agent_tool_utils import (
    extract_partial_result,
    finalize_agent_tool,
)
from src.agent.constants import (
    AGENT_TOOL_NAME,
    FORK_SUBAGENT_TYPE,
    LEGACY_AGENT_TOOL_NAME,
    ONE_SHOT_BUILTIN_AGENT_TYPES,
)
from src.agent.fork_subagent import (
    build_forked_messages,
    build_worktree_notice,
    is_fork_subagent_enabled,
    is_in_fork_child,
)
from src.agent.prompt import get_agent_prompt, get_agent_system_prompt
from src.agent.run_agent import RunAgentParams, run_agent

logger = logging.getLogger(__name__)

# Input schema matching typescript/src/tools/AgentTool/AgentTool.tsx
AGENT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "description": {
            "type": "string",
            "description": "A short (3-5 word) description of the task",
        },
        "prompt": {
            "type": "string",
            "description": "The task for the agent to perform",
        },
        "subagent_type": {
            "type": "string",
            "description": "The type of specialized agent to use for this task",
        },
        "model": {
            "type": "string",
            "description": (
                "Optional model override for this agent. Takes precedence over "
                "the agent definition's model frontmatter. If omitted, uses the "
                "agent definition's model, or inherits from the parent."
            ),
            "enum": ["sonnet", "opus", "haiku"],
        },
        "run_in_background": {
            "type": "boolean",
            "description": (
                "Set to true to run this agent in the background. "
                "You will be notified when it completes."
            ),
        },
        "isolation": {
            "type": "string",
            "description": (
                "Isolation mode. \"worktree\" creates a temporary git worktree "
                "so the agent works on an isolated copy of the repo."
            ),
            "enum": ["worktree"],
        },
        # Chapter-10 / Chunk F / WI-6.1: optional human-readable name.
        # When set, the spawned agent is reachable via
        # ``SendMessage({to: name})`` instead of the raw agent_id.
        # Mirrors TS AgentTool's ``name`` parameter; the registry
        # collision policy is enforced inside ``_launch_async_agent``.
        "name": {
            "type": "string",
            "description": (
                "Optional name for the spawned agent. Makes it addressable via "
                "SendMessage({to: name}) while running. Errors if the name is "
                "already in use by a running agent; overwrites a terminal one."
            ),
        },
    },
    "required": ["description", "prompt"],
}


def make_agent_tool(
    registry: ToolRegistry,
    provider: Any | None = None,
    get_available_mcp_servers: Any | None = None,
) -> Tool:
    """Build the Agent tool.

    Mirrors the AgentTool definition from typescript/src/tools/AgentTool/AgentTool.tsx.

    Args:
        registry: Tool registry providing the available tool pool.
        provider: BaseProvider for API calls. If None, agent execution is a no-op
                  (useful for testing tool registration only).
        get_available_mcp_servers: Optional zero-arg callable returning the
            currently-available MCP server names. Used by the prompt builder so
            agents declaring ``required_mcp_servers`` not present in the live
            inventory are hidden from the tool description (matching the
            per-call resolver). When ``None`` the prompt advertises every
            discovered agent unfiltered.
    """
    def _get_agent_definitions(context: ToolContext) -> list[AgentDefinition]:
        """Resolve agents visible to this call.

        SDK / test callers can pre-populate ``options.agent_definitions
        ["active_agents"]`` to override discovery. Otherwise we walk the
        managed / user / project ``agents`` directories via
        ``get_agent_definitions_with_overrides`` and apply the MCP filter
        keyed on the context's available MCP server inventory.
        """
        agent_defs = getattr(context.options, "agent_definitions", None)
        if agent_defs and isinstance(agent_defs, dict):
            active = agent_defs.get("active_agents")
            if active and isinstance(active, list):
                return active
        cwd = str(context.cwd or context.workspace_root)
        agents = get_agent_definitions_with_overrides(cwd)
        available_mcp = list(context.mcp_clients.keys()) if context.mcp_clients else []
        return filter_agents_by_mcp_requirements(agents, available_mcp)

    def _agent_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        prompt = tool_input.get("prompt", "")
        if not prompt:
            raise ToolInputError("prompt is required")

        description = tool_input.get("description", prompt[:50])
        subagent_type = tool_input.get("subagent_type")
        model = tool_input.get("model")
        run_in_background = bool(tool_input.get("run_in_background", False))
        # Chapter-10 / WI-6.1 — optional human-readable name. We
        # validate / register it AFTER agent_id is generated so the
        # collision-on-running check can compare against the registry
        # state under the registry's own atomicity guarantees.
        agent_name = tool_input.get("name")
        if agent_name is not None and not isinstance(agent_name, str):
            raise ToolInputError("name must be a string when provided")
        if isinstance(agent_name, str) and not agent_name.strip():
            agent_name = None  # treat empty/whitespace as absent

        # Resolve agent definition.
        #
        # Routing rules mirror typescript/src/tools/AgentTool/AgentTool.tsx:318-356:
        # - subagent_type provided → use it (explicit wins).
        # - subagent_type omitted, fork gate on → implicit fork via FORK_AGENT.
        # - subagent_type omitted, fork gate off → default to general-purpose.
        agent_definitions = _get_agent_definitions(context)
        is_fork_path = (
            subagent_type is None and is_fork_subagent_enabled(context)
        )

        if is_fork_path:
            # Recursive-fork guard. Primary check: querySource on the parent's
            # options. Secondary check: scan parent messages for the fork
            # boilerplate tag. Either one trips means we are already inside a
            # fork child, so refuse to spawn another.
            parent_query_source = getattr(context.options, "query_source", None)
            if parent_query_source == f"agent:builtin:{FORK_SUBAGENT_TYPE}" or is_in_fork_child(context.messages):
                raise ToolInputError(
                    "Fork is not available inside a forked worker. "
                    "Complete your task directly using your tools."
                )
            agent_def = FORK_AGENT
        elif subagent_type:
            agent_def = find_agent_by_type(agent_definitions, subagent_type)
            if agent_def is None:
                available = [a.agent_type for a in agent_definitions]
                raise ToolInputError(
                    f"Unknown subagent_type: {subagent_type}. "
                    f"Available: {', '.join(available)}"
                )
        else:
            # Default to general-purpose
            agent_def = (
                find_agent_by_type(agent_definitions, "general-purpose")
                or agent_definitions[0]
                if agent_definitions
                else None
            )
            if agent_def is None:
                raise ToolInputError("No agent definitions available")

        # Resolve available tools
        available_tools = registry.list_tools()

        # Chapter-10 / WI-1.5: prefixed task id (``a<8 base36 chars>``)
        # mirroring TS Task.ts:79-105. Replaces the legacy 32-char
        # ``uuid4().hex`` so SendMessage / TaskStop dispatch keys are
        # uniform across types.
        from src.tasks_core import generate_task_id  # local import — see _launch_async_agent
        agent_id = generate_task_id("local_agent")
        start_time = time.time()
        is_async = run_in_background

        if provider is None:
            return ToolResult(
                name=AGENT_TOOL_NAME,
                output={
                    "status": "error",
                    "error": "No provider configured — agent execution unavailable.",
                },
                is_error=True,
            )

        # Fork-specific run-agent inputs.
        #
        # On the fork path:
        #   * The child inherits the parent's full conversation as
        #     ``context_messages`` so the API-request prefix matches the
        #     parent's most recent turn.
        #   * ``build_forked_messages()`` produces the trailing pair: a
        #     cloned parent assistant message plus a single user message
        #     carrying placeholder tool_results and the boilerplate-wrapped
        #     directive. This pair is passed as a single concatenated
        #     ``prompt`` (run_agent appends a UserMessage built from
        #     ``params.prompt``); to preserve the cloned-assistant block we
        #     instead route the messages via ``context_messages`` and pass
        #     the directive bytes alone as ``params.prompt``.
        #   * ``use_exact_tools`` skips ``resolve_agent_tools()`` so the
        #     child's tool array is byte-identical to the parent's.
        #   * ``query_source`` is threaded onto the child's options for the
        #     primary recursive-fork guard at the next call site.
        #   * ``parent_system_prompt`` carries the parent's resolved prompt
        #     so the fork agent's empty get_system_prompt is filled in via
        #     ``get_agent_system_prompt()``.
        fork_context_messages: list[Any] | None = None
        fork_query_source: str | None = None
        fork_use_exact_tools = False
        fork_parent_system_prompt: "str | list | None" = None
        fork_prompt = prompt

        if is_fork_path:
            from src.types.messages import AssistantMessage, create_user_message

            parent_assistant: AssistantMessage | None = None
            for msg in reversed(context.messages):
                if isinstance(msg, AssistantMessage):
                    parent_assistant = msg
                    break

            forked_pair = build_forked_messages(prompt, parent_assistant)

            # Fork + worktree: append the translation notice as a trailing
            # user message so it appears as the most recent guidance the
            # child sees. Mirrors
            # ``typescript/src/tools/AgentTool/AgentTool.tsx:610-614``. The
            # notice is plain text — it does NOT contain the
            # ``<fork-boilerplate>`` tag, so the message-scan recursion guard
            # is unaffected.
            worktree_cwd = _resolve_fork_worktree_cwd(context)
            if worktree_cwd is not None:
                parent_cwd_str = str(context.cwd or context.workspace_root)
                notice_text = build_worktree_notice(parent_cwd_str, worktree_cwd)
                forked_pair = list(forked_pair) + [
                    create_user_message(content=notice_text)
                ]

            fork_context_messages = list(context.messages) + forked_pair
            fork_use_exact_tools = True
            fork_query_source = f"agent:builtin:{FORK_SUBAGENT_TYPE}"
            # Parent system prompt: prefers ``context.rendered_system_prompt``
            # (byte-identical to the parent's last API call) and falls back
            # to recomputing via the active agent def. See
            # ``_resolve_parent_system_prompt`` docstring for the full
            # cascade, which mirrors ``AgentTool.tsx:495-511``.
            fork_parent_system_prompt = _resolve_parent_system_prompt(context, agent_definitions)
            # ``run_agent`` will append a UserMessage built from ``prompt``
            # to whatever ``context_messages`` it receives. We have already
            # placed the directive inside ``forked_pair`` via
            # ``build_forked_messages``; pass an empty prompt so we don't
            # duplicate it.
            fork_prompt = ""

        run_params = RunAgentParams(
            parent_context=context,
            agent_definition=agent_def,
            prompt=fork_prompt,
            available_tools=available_tools,
            tool_registry=registry,
            provider=provider,
            model=model,
            agent_id=agent_id,
            is_async=is_async,
            max_turns=agent_def.max_turns,
            context_messages=fork_context_messages,
            use_exact_tools=fork_use_exact_tools,
            query_source=fork_query_source,
            parent_system_prompt=fork_parent_system_prompt,
        )

        # Stream the subagent's live progress to the UI when the host wired a
        # hook (agent-server only). run_agent calls on_message per message, so
        # this covers both the sync and background paths. Purely additive — no
        # hook means no behavior change.
        _emit_progress = getattr(context, "agent_progress_emit", None)
        if _emit_progress is not None:
            from src.tasks.progress import (
                ProgressTracker,
                total_tokens_from_tracker,
                update_progress_from_message,
            )

            _tracker = ProgressTracker()

            def _on_subagent_message(message: Any) -> None:
                try:
                    update_progress_from_message(_tracker, message)
                    acts = _tracker.recent_activities
                    activity = None
                    if acts:
                        last = acts[-1]
                        activity = last.activity_description or last.tool_name
                    _emit_progress({
                        "agent_id": agent_id,
                        "name": agent_name,
                        "description": description,
                        "subagent_type": subagent_type,
                        "activity": activity,
                        "tool_use_count": _tracker.tool_use_count,
                        "tokens": total_tokens_from_tracker(_tracker),
                        "status": "running",
                    })
                except Exception:
                    logger.debug("subagent progress emit failed", exc_info=True)

            run_params.on_message = _on_subagent_message

        if is_async:
            return _launch_async_agent(
                run_params=run_params,
                context=context,
                agent_id=agent_id,
                description=description,
                prompt=prompt,
                agent_type=agent_def.agent_type,
                agent_name=agent_name,
            )
        else:
            return _run_sync_agent(
                run_params=run_params,
                agent_id=agent_id,
                start_time=start_time,
                prompt=prompt,
                agent_type=agent_def.agent_type,
            )

    def _run_sync_agent(
        *,
        run_params: RunAgentParams,
        agent_id: str,
        start_time: float,
        prompt: str,
        agent_type: str,
    ) -> ToolResult:
        """Run an agent synchronously and return the result."""
        from ..protocol import ToolResult as TR
        from src.types.messages import Message

        agent_messages: list[Message] = []

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context — use a nested run
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(_sync_collect_agent_messages, run_params)
                    agent_messages = future.result()
            else:
                agent_messages = loop.run_until_complete(
                    _collect_agent_messages(run_params)
                )
        except RuntimeError:
            # No event loop — create one
            agent_messages = asyncio.run(
                _collect_agent_messages(run_params)
            )

        # Finalize result
        metadata = {
            "start_time": start_time,
            "agent_type": agent_type,
        }
        result = finalize_agent_tool(agent_messages, agent_id, metadata)

        return TR(
            name=AGENT_TOOL_NAME,
            output={
                "status": "completed",
                "prompt": prompt,
                "agent_id": result.agent_id,
                "agent_type": result.agent_type,
                "content": result.content,
                "total_duration_ms": result.total_duration_ms,
                "total_tokens": result.total_tokens,
                "total_tool_use_count": result.total_tool_use_count,
            },
        )

    def _launch_async_agent(
        *,
        run_params: RunAgentParams,
        context: ToolContext,
        agent_id: str,
        description: str,
        prompt: str,
        agent_type: str,
        agent_name: str | None = None,
    ) -> ToolResult:
        """Launch an agent in the background and return immediately.

        Chapter-10 layered story:
        * Chunk B / WI-1.5 — state on ``context.runtime_tasks`` as a
          typed ``LocalAgentTaskState`` (no more ``context.tasks`` /
          ``metadata._internal=True`` workaround).
        * Chunk C / WI-2.2 (gate-zero) — sidechain JSONL transcript
          opened for the lifetime of the agent run; ``output_file`` is
          its absolute path.
        * Chunk C / WI-2.3 — lifecycle goes through the named helpers
          (``register_async_agent`` / ``complete_agent_task`` /
          ``fail_agent_task``) so the registry mutations are atomic
          and consistent across spawn / kill / completion paths.
        * Chunk C / WI-2.4 — token accounting via ``ProgressTracker``;
          ``finalize_agent_tool`` reads its accumulated totals instead
          of reporting ``total_tokens=0``.
        * Chunk F / WI-6.1 — optional ``agent_name`` registers the
          spawn under ``context.agent_name_registry`` so SendMessage
          can resolve ``to: <name>``. Collision-on-running raises;
          collision-on-terminal silently overwrites.
        """
        # Local imports defer the cycle: ``src.tasks.local_agent``
        # reaches back into ``src.task_registry`` which is fine, but
        # importing them at module scope would tangle with
        # ``defaults.py``'s tool-construction order.
        from src.agent.transcript import TranscriptWriter
        from src.tasks.local_agent import (
            LocalAgentTaskState,
            complete_agent_task,
            fail_agent_task,
            register_async_agent,
        )
        from src.tasks.progress import (
            ProgressTracker,
            update_progress_from_message,
        )
        from src.services.swarm.agent_name_registry import (
            AgentNameAlreadyClaimedError,
        )
        from src.utils.task_notification import enqueue_agent_notification

        # WI-6.1 + critic C1 (Phase-7 fix): atomic check-and-claim
        # under the typed registry's RLock. The previous Phase-6
        # implementation had a TOCTOU window between the read and
        # the write; the typed ``claim_or_raise`` closes it. We do
        # the claim BEFORE the runtime_tasks write so a refused
        # spawn doesn't leak a half-constructed agent_id into the
        # runtime registry.
        if agent_name is not None:
            try:
                context.agent_name_registry.claim_or_raise(
                    agent_name, agent_id, context.runtime_tasks,
                )
            except AgentNameAlreadyClaimedError as exc:
                raise ToolInputError(str(exc)) from exc

        register_async_agent(
            agent_id=agent_id,
            description=description,
            prompt=prompt,
            agent_type=agent_type,
            registry=context.runtime_tasks,
        )
        # ``register_async_agent`` populated ``output_file`` with the
        # JSONL transcript path; pull it back so the writer points at
        # the same path the lifecycle helpers committed to.
        registered = context.runtime_tasks.get(agent_id)
        transcript_path = (
            registered.output_file
            if isinstance(registered, LocalAgentTaskState)
            else ""
        )

        async def _background_lifecycle() -> None:
            tracker = ProgressTracker()
            messages: list[Any] = []
            transcript: TranscriptWriter | None = None
            if transcript_path:
                try:
                    transcript = TranscriptWriter(transcript_path)
                except OSError:
                    # Transcript open failure must not abort the run —
                    # downstream Chunk D / Chunk F will degrade
                    # gracefully (no outputFile content / no auto-resume
                    # source) rather than crash.
                    logger.exception(
                        "transcript open failed for %s; continuing without disk persistence",
                        agent_id,
                    )
                    transcript = None
            try:
                try:
                    async for message in run_agent(run_params):
                        messages.append(message)
                        # Live progress accounting — feeds the post-hoc
                        # ``finalize_agent_tool`` token total via the
                        # ``progress`` keyword (WI-2.4 fallback also
                        # works if the tracker is somehow empty).
                        try:
                            update_progress_from_message(tracker, message)
                        except Exception:
                            logger.exception(
                                "progress tracker update failed for %s", agent_id
                            )
                        # Persist to disk per WI-2.2. Synchronous IO
                        # outside the registry lock — A6/C5 contract is
                        # preserved (no ``await`` under the registry's
                        # RLock).
                        if transcript is not None:
                            try:
                                transcript.append(message)
                            except OSError:
                                logger.exception(
                                    "transcript append failed for %s; further appends will be skipped",
                                    agent_id,
                                )
                                transcript.close()
                                transcript = None

                    metadata = {
                        "start_time": time.time(),
                        "agent_type": agent_type,
                    }
                    result = finalize_agent_tool(
                        messages, agent_id, metadata, progress=tracker
                    )
                    result_text = "\n".join(
                        block.get("text", "")
                        for block in result.content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ).strip()
                    if not result_text:
                        result_text = "(Subagent completed with no textual output.)"

                    complete_agent_task(
                        agent_id,
                        result_text=result_text,
                        registry=context.runtime_tasks,
                    )
                    # Chunk D / WI-3.1 + WI-3.2 — enqueue a single
                    # ``<task-notification>`` envelope. Atomic check-and-
                    # set on ``state.notified`` inside the helper means
                    # a concurrent kill / fail / completion path can't
                    # produce a second envelope.
                    enqueue_agent_notification(
                        task_id=agent_id,
                        description=description,
                        status="completed",
                        output_file=transcript_path,
                        final_message=result_text,
                        usage={
                            "total_tokens": result.total_tokens,
                            "tool_uses": result.total_tool_use_count,
                            "duration_ms": result.total_duration_ms,
                        },
                        registry=context.runtime_tasks,
                    )
                    logger.info(
                        "Async agent %s (%s) finished: %d messages, %d tokens",
                        agent_id, agent_type, len(messages), result.total_tokens,
                    )
                except Exception as exc:
                    partial = extract_partial_result(messages)
                    err_text = partial or str(exc)
                    fail_agent_task(
                        agent_id,
                        error=err_text,
                        registry=context.runtime_tasks,
                    )
                    enqueue_agent_notification(
                        task_id=agent_id,
                        description=description,
                        status="failed",
                        output_file=transcript_path,
                        error=str(exc),
                        final_message=partial,
                        registry=context.runtime_tasks,
                    )
                    logger.exception(
                        "Async agent %s (%s) failed",
                        agent_id, agent_type,
                    )
            finally:
                if transcript is not None:
                    transcript.close()

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is not None:
            running_loop.create_task(_background_lifecycle())
        else:
            def _runner(_stop_event: Any) -> None:
                asyncio.run(_background_lifecycle())

            context.task_manager.start(name=f"agent:{agent_type}", target=_runner)

        return ToolResult(
            name=AGENT_TOOL_NAME,
            output={
                "status": "async_launched",
                "agent_id": agent_id,
                "agent_type": agent_type,
                "description": description,
                "prompt": prompt,
                "task_output_key": agent_id,
            },
        )

    def _agent_prompt() -> str:
        """Build the prompt for the Agent tool.

        Includes built-in agents plus any custom agents discovered on
        disk so the model sees the full set of valid ``subagent_type``
        values in the tool description. When ``get_available_mcp_servers``
        was supplied at tool construction, the MCP filter runs here too —
        otherwise the prompt advertises every discovered agent and the
        per-call resolver enforces availability at spawn time.
        """
        try:
            agents = get_agent_definitions_with_overrides(os.getcwd())
        except Exception:
            logger.exception("agent discovery failed in tool prompt; using built-ins")
            agents = list(get_built_in_agents())
        if get_available_mcp_servers is not None:
            try:
                available = list(get_available_mcp_servers() or [])
            except Exception:
                logger.exception(
                    "get_available_mcp_servers raised; treating as no MCPs "
                    "available — agents requiring MCP servers will be hidden"
                )
                available = []
            agents = filter_agents_by_mcp_requirements(agents, available)
        return get_agent_prompt(agents)

    def _map_result_to_api(result: Any, tool_use_id: str) -> dict[str, Any]:
        if not isinstance(result, dict):
            return {"type": "tool_result", "tool_use_id": tool_use_id, "content": str(result)}
        content = result.get("content", "")
        if result.get("status") == "error":
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result.get("error", content),
                "is_error": True,
            }
        if result.get("status") == "async_launched":
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": (
                    "Async agent launched successfully.\n"
                    f"agent_id: {result.get('agent_id', '')}\n"
                    f"task_output_key: {result.get('task_output_key', '')}\n"
                    "Use TaskOutput with task_id equal to task_output_key to check completion."
                ),
            }
        if result.get("status") == "completed":
            text_parts: list[str] = []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str) and text.strip():
                            text_parts.append(text.strip())
            elif isinstance(content, str) and content.strip():
                text_parts.append(content.strip())
            rendered = "\n\n".join(text_parts).strip() or "(Subagent completed with no textual output.)"
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": rendered,
            }
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}

    return build_tool(
        name=AGENT_TOOL_NAME,
        input_schema=AGENT_INPUT_SCHEMA,
        call=_agent_call,
        prompt=_agent_prompt,
        description=lambda _input: "Launch a new agent to handle a task",
        aliases=(LEGACY_AGENT_TOOL_NAME,),
        map_result_to_api=_map_result_to_api,
        max_result_size_chars=200_000,
        is_destructive=lambda _input: True,
        # ch07 round-4 GAP A — the Agent tool is concurrency-safe (TS
        # AgentTool.tsx:1288-1290, and the tool prompt tells the model to
        # launch multiple agents concurrently). A run of consecutive Agent
        # tool_use blocks now partitions into ONE parallel batch instead of
        # N serial batches, so default foreground multi-agent fan-out runs
        # concurrently (bounded by MAX_TOOL_USE_CONCURRENCY). Safe: sync
        # tool calls run on worker threads (tool_execution.py:558
        # asyncio.to_thread), sub-agents get fully isolated contexts +
        # abort controllers, and the model is responsible for not spawning
        # conflicting agents (same contract as TS). NOTE: is_read_only
        # stays False (unset) — unlike TS's isReadOnly()=true — because the
        # port's sub-agents run Edit/Write, so Agent is not read-only in
        # effect; concurrency-safety != read-only.
        is_concurrency_safe=lambda _input: True,
        search_hint="agent spawn subagent delegate task",
        to_auto_classifier_input=lambda input_data: (input_data or {}).get("prompt", "")[:200],
        get_activity_description=lambda input_data: (input_data or {}).get("description", "Running agent") if input_data else None,
    )


def _resolve_parent_system_prompt(
    context: ToolContext,
    agent_definitions: list[AgentDefinition],
) -> "str | list | None":
    """Resolve the parent system prompt for fork children.

    Mirrors the layered fallback in
    ``typescript/src/tools/AgentTool/AgentTool.tsx:495-511``:

    1. ``context.rendered_system_prompt`` — the bytes captured from the
       parent's most recent API call. Preferred path: identical to the
       parent's cached prefix, so the fork child's API request hits the
       prompt cache without recomputing anything that might have shifted
       (chapter 9 §"The Byte-Identical Prefix Trick", Layer 1).
    2. ``context.options.custom_system_prompt`` — explicit caller
       override.
    3. The active agent definition's ``get_system_prompt()`` output —
       recompute fallback. Useful for tests and SDK callers that never
       populated the rendered field but still want a coherent prompt.
    4. ``None`` — let ``get_agent_system_prompt`` fall through to
       ``DEFAULT_AGENT_PROMPT``.

    Returns ``None`` when no candidate is available.
    """
    rendered = getattr(context, "rendered_system_prompt", None)
    # ch09 round-4 WI-1 — accept the parent's actual prompt shape. On the
    # live path this is a non-empty list[dict] of system blocks; on
    # string-prompt callers it is a str. Both are threaded verbatim into
    # the fork child's system_prompt for byte-identity.
    if isinstance(rendered, list) and rendered:
        return rendered
    if isinstance(rendered, str) and rendered.strip():
        return rendered

    custom = getattr(context.options, "custom_system_prompt", None)
    if isinstance(custom, str) and custom.strip():
        return custom

    active_type = getattr(context, "agent_type", None)
    if active_type:
        active_def = find_agent_by_type(agent_definitions, active_type)
        if active_def is not None:
            try:
                return active_def.get_system_prompt()
            except Exception:
                return None

    return None


def _resolve_fork_worktree_cwd(context: ToolContext) -> str | None:
    """Return the worktree cwd string for a fork child, or ``None``.

    Mirrors the ``isForkPath && worktreeInfo`` branch in
    ``typescript/src/tools/AgentTool/AgentTool.tsx:610-614``. Only return
    a non-None value when the active context has a worktree root that
    differs from the parent's working directory — otherwise the notice
    would be misleading ("you are operating in an isolated worktree at
    /same/path").
    """
    wt_root = getattr(context, "worktree_root", None)
    if wt_root is None:
        return None
    wt_path = str(wt_root)
    parent_cwd = str(context.cwd or context.workspace_root)
    if not wt_path or wt_path == parent_cwd:
        return None
    return wt_path


def _sync_collect_agent_messages(params: RunAgentParams) -> list[Any]:
    """Collect agent messages synchronously in a new event loop."""
    return asyncio.run(_collect_agent_messages(params))


def _format_subagent_tool_use(agent_type: str, name: str, tool_input: Any) -> str:
    """Format one nested tool_use into the ``⎿ [type] Name(args)`` line.

    Parity gap fix: the original implementation hard-coded ``Name(...)`` which
    discards the file path / command / pattern the user needs to follow what
    the subagent is doing. TS's ``getActivityDescription`` (e.g.
    ``FileReadTool.ts:369``) renders the same input data into a per-tool
    sentence; the closest Python equivalent already exists in
    ``summarize_tool_use``, so we route through it.

    Empty summaries fall back to ``Name`` (no parens) so a tool whose summarizer
    returned nothing still produces a clean line instead of literal ``Name()``.
    """
    from src.tool_system.renderers import summarize_tool_use

    safe_input: dict[str, Any] = tool_input if isinstance(tool_input, dict) else {}
    summary = ""
    try:
        summary = summarize_tool_use(name, safe_input) or ""
    except Exception:
        # A buggy summarizer must not poison live progress output.
        summary = ""
    if summary:
        # Keep the line single-row even when summaries embed newlines (Bash
        # ``$ cmd\ncmd2``) or are pathologically long.
        flat = summary.replace("\n", " ").strip()
        if len(flat) > 200:
            flat = flat[:197] + "..."
        call = f"{name}({flat})"
    else:
        call = name
    return f"  ⎿ [{agent_type}] {call}\n"


async def _collect_agent_messages(params: RunAgentParams) -> list[Any]:
    """Collect all messages from the run_agent generator.

    Prints intermediate agent messages (explanatory text, tool use summaries)
    to stderr so the user sees progress in real-time instead of a silent wait.
    """
    from src.types.messages import Message, AssistantMessage
    from src.types.content_blocks import TextBlock, ToolUseBlock

    agent_type = getattr(params.agent_definition, 'agent_type', 'agent')
    messages: list[Message] = []
    async for msg in run_agent(params):
        messages.append(msg)

        # Print intermediate progress to stderr for real-time feedback
        if isinstance(msg, AssistantMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                sys.stderr.write(f"  ⎿ [{agent_type}] {content.strip()[:200]}\n")
                sys.stderr.flush()
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        sys.stderr.write(f"  ⎿ [{agent_type}] {block.text.strip()[:200]}\n")
                        sys.stderr.flush()
                    elif isinstance(block, ToolUseBlock):
                        sys.stderr.write(
                            _format_subagent_tool_use(
                                agent_type, block.name, getattr(block, "input", None)
                            )
                        )
                        sys.stderr.flush()
    return messages
