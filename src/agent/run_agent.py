"""Agent lifecycle management.

Mirrors typescript/src/tools/AgentTool/runAgent.ts and forkedAgent.ts.
Provides the core run_agent() async generator and filter_incomplete_tool_calls().
"""
from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator
from uuid import uuid4

from ..permissions.types import PermissionMode, ToolPermissionContext
from ..providers.base import BaseProvider
from ..tool_system.build_tool import Tools
from ..tool_system.context import ToolContext
from ..tool_system.registry import ToolRegistry
from ..types.content_blocks import ToolUseBlock
from ..types.messages import AssistantMessage, Message, UserMessage
from ..utils.abort_controller import AbortController

from .agent_definitions import AgentDefinition, is_built_in_agent
from .agent_tool_utils import (
    count_tool_uses,
    get_query_source_for_agent,
    resolve_agent_tools,
)
from .constants import AGENT_TOOL_NAME
from .prompt import get_agent_system_prompt
from .subagent_context import SubagentContextOverrides, create_subagent_context

logger = logging.getLogger(__name__)

# Fallback max turns for subagents when no explicit limit is set.
# Prevents unbounded loops that appear as hangs to the user.
SUBAGENT_DEFAULT_MAX_TURNS = 30


@dataclass
class RunAgentParams:
    """Parameters for running an agent.

    Mirrors the parameters accepted by the runAgent() generator in TypeScript.
    """
    parent_context: ToolContext
    agent_definition: AgentDefinition
    prompt: str
    available_tools: Tools
    tool_registry: ToolRegistry
    provider: BaseProvider

    # Optional overrides
    model: str | None = None
    agent_id: str | None = None
    # QUERY-1 — the spawn's addressable name (Agent tool `name` param).
    # A NAMED agent inside a team is this port's teammate: the identity is
    # threaded to the subagent context so teammate stop hooks can gate.
    agent_name: str | None = None
    is_async: bool = False
    max_turns: int | None = None
    system_prompt_override: str | None = None
    parent_system_prompt: "str | list | None" = None
    permission_mode_override: PermissionMode | None = None
    context_messages: list[Message] | None = None
    abort_controller: AbortController | None = None
    on_message: Any = None
    # When True, use ``available_tools`` directly without filtering through
    # ``resolve_agent_tools()``. Mirrors the ``useExactTools`` flag from
    # typescript/src/tools/AgentTool/runAgent.ts (the fork path).
    use_exact_tools: bool = False
    # Identifier threaded into ``ToolUseOptions.query_source`` for the
    # primary recursive-fork guard. Mirrors the TS ``querySource`` argument.
    query_source: str | None = None


@dataclass
class RunAgentResult:
    """Aggregated result of an agent run."""
    messages: list[Message]
    agent_id: str
    agent_type: str
    start_time: float
    total_tool_use_count: int = 0
    total_tokens: int = 0


def resolve_permission_mode(
    parent_context: ToolContext,
    agent_definition: AgentDefinition,
    is_async: bool = False,
) -> PermissionMode:
    """Resolve the effective permission mode for a subagent.

    Mirrors the permission inheritance logic from typescript/src/tools/AgentTool/runAgent.ts.

    Rules:
    - Parent bypassPermissions/acceptEdits/dontAsk → parent takes precedence
    - Parent plan/default + agent defines permissionMode → agent overrides
    - Async agents always get should_avoid_permission_prompts=True (handled in context creation)
    """
    parent_mode = parent_context.permission_context.mode

    # Permissive parent modes always take precedence
    if parent_mode in ("bypassPermissions", "acceptEdits", "dontAsk"):
        return parent_mode

    # Agent can override restrictive parent modes (plan, default)
    if agent_definition.permission_mode is not None:
        return agent_definition.permission_mode

    # Fall through to parent mode
    return parent_mode


def _build_permission_context(
    parent_context: ToolContext,
    effective_mode: PermissionMode,
    is_async: bool,
) -> ToolPermissionContext:
    """Build the permission context for the subagent.

    Mirrors the prompt-avoidance cascade in
    ``typescript/src/tools/AgentTool/runAgent.ts:449-476``:

    1. ``should_avoid_permission_prompts`` is True iff the parent already
       avoids prompts OR the agent is async AND its effective mode is
       not ``bubble``. Bubble mode preserves prompts even when async,
       because the bubble path surfaces them to the parent terminal.
    2. ``await_automated_checks_before_dialog`` is True for async agents
       whose prompts are still enabled — today that means bubble mode.
       It signals the permission system to run the classifier / hooks
       before interrupting the user.

    The TS implementation also reads ``canShowPermissionPrompts`` as an
    explicit caller override. The Python ``RunAgentParams`` does not yet
    plumb that flag, so the cascade reduces to the ``isAsync`` /
    ``agentPermissionMode === 'bubble'`` branch. Round-2 docs flag the
    full ``canShowPermissionPrompts`` thread-through as out-of-scope.
    """
    parent_perm = parent_context.permission_context

    # TS cascade: bubble mode is the only async mode that can still
    # show prompts (they bubble to the parent terminal). Every other
    # async agent must auto-deny rather than block on a missing UI.
    if effective_mode == "bubble":
        avoid_for_isolation = False
    else:
        avoid_for_isolation = is_async
    should_avoid = (
        parent_perm.should_avoid_permission_prompts or avoid_for_isolation
    )

    # Async-but-can-prompt agents wait for classifier / hooks before
    # interrupting the user. Sync agents and prompt-avoiding agents
    # skip this — there is either no async to wait inside of, or no
    # dialog to delay.
    await_automated = is_async and not should_avoid

    return ToolPermissionContext(
        mode=effective_mode,
        additional_working_directories=parent_perm.additional_working_directories,
        always_allow_rules=parent_perm.always_allow_rules,
        always_deny_rules=parent_perm.always_deny_rules,
        always_ask_rules=parent_perm.always_ask_rules,
        is_bypass_permissions_mode_available=parent_perm.is_bypass_permissions_mode_available,
        should_avoid_permission_prompts=should_avoid,
        await_automated_checks_before_dialog=await_automated,
    )


def filter_incomplete_tool_calls(messages: list[Message]) -> list[Message]:
    """Remove assistant messages that contain incomplete tool_use blocks.

    Mirrors filterIncompleteToolCalls() from typescript/src/tools/AgentTool/runAgent.ts.

    Assistant messages with tool_use blocks are only valid if each tool_use has a
    corresponding tool_result block in a user message. This function removes any
    assistant message containing unresolved tool_use IDs.
    """
    tool_use_ids_with_results: set[str] = set()

    for message in messages:
        if not isinstance(message, UserMessage):
            continue
        content = message.content
        if not isinstance(content, list):
            continue
        for block in content:
            block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if block_type != "tool_result":
                continue
            tool_use_id = (
                block.get("tool_use_id")
                if isinstance(block, dict)
                else getattr(block, "tool_use_id", None)
            )
            if isinstance(tool_use_id, str) and tool_use_id:
                tool_use_ids_with_results.add(tool_use_id)

    filtered: list[Message] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            content = message.content
            if isinstance(content, list):
                has_incomplete_tool_call = False
                for block in content:
                    block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                    if block_type != "tool_use":
                        continue
                    tool_use_id = (
                        block.get("id")
                        if isinstance(block, dict)
                        else getattr(block, "id", None)
                    )
                    if isinstance(tool_use_id, str) and tool_use_id not in tool_use_ids_with_results:
                        has_incomplete_tool_call = True
                        break
                if has_incomplete_tool_call:
                    continue
        filtered.append(message)

    return filtered


async def run_agent(params: RunAgentParams) -> AsyncGenerator[Message, None]:
    """Run an agent's query loop and yield messages.

    Mirrors the runAgent() async generator from typescript/src/tools/AgentTool/runAgent.ts.

    This function:
    1. Resolves model, tools, system prompt, and permission mode
    2. Creates an isolated subagent context
    3. Runs the query loop via the existing query() function
    4. Yields messages as they arrive
    5. Cleans up on completion or abort
    """
    from ..query.query import QueryParams, StreamEvent, query

    # --- Setup ---
    agent_def = params.agent_definition
    agent_id = params.agent_id or uuid4().hex
    start_time = time.time()
    agent_messages: list[Message] = []

    # Resolve permission mode. An explicit ``permission_mode_override`` wins
    # (used by the workflow engine to force ``acceptEdits`` for its subagents);
    # otherwise fall back to the inheritance rules.
    effective_mode = params.permission_mode_override or resolve_permission_mode(
        params.parent_context,
        agent_def,
        is_async=params.is_async,
    )

    # Resolve tools for the agent.
    # When ``use_exact_tools`` is set (fork path), bypass ``resolve_agent_tools``
    # so the child receives the parent's exact tool array. Mirrors the
    # ``useExactTools`` branch in typescript/src/tools/AgentTool/runAgent.ts:513.
    if params.use_exact_tools:
        agent_tools = list(params.available_tools)
    else:
        resolved = resolve_agent_tools(
            agent_def,
            params.available_tools,
            is_async=params.is_async,
        )
        agent_tools = resolved.resolved_tools

        if resolved.invalid_tools:
            logger.warning(
                "Agent %s has invalid tools: %s",
                agent_def.agent_type,
                resolved.invalid_tools,
            )

    # Build system prompt
    system_prompt = (
        params.system_prompt_override
        or get_agent_system_prompt(agent_def, params.parent_system_prompt)
    )

    # Determine abort controller.
    # ``params.parent_context.abort_controller`` is now non-optional on
    # the ``ToolContext`` dataclass, so the legacy "parent has no
    # controller → mint a fresh one" branch is gone. The remaining
    # priority order is: explicit caller override → fresh controller
    # for async (so background agents survive parent cancel) →
    # share with parent for sync (so parent ESC propagates).
    if params.abort_controller is not None:
        abort_controller = params.abort_controller
    elif params.is_async:
        # Async agents run independently in the background and should survive
        # parent cancellation events.
        abort_controller = AbortController()
    else:
        # Sync agents share abort with parent
        abort_controller = params.parent_context.abort_controller

    # Build permission context
    perm_context = _build_permission_context(
        params.parent_context,
        effective_mode,
        params.is_async,
    )

    # Strip orphaned tool_use blocks before threading parent context into the
    # child. Mirrors typescript/src/tools/AgentTool/runAgent.ts:381-385 — the
    # API rejects assistant messages whose tool_use IDs lack matching
    # tool_result IDs.
    if params.context_messages:
        sanitized_context_messages = filter_incomplete_tool_calls(params.context_messages)
    else:
        sanitized_context_messages = []

    # When the fork path threads its own ``query_source`` (e.g.
    # ``"agent:builtin:fork"``), surface it on the child's options so the
    # primary recursive-fork guard at the Agent tool's call site can read it.
    options_override = None
    if params.query_source is not None:
        # Shallow-copy the parent options so we don't mutate them in place.
        from copy import copy as _shallow_copy
        options_override = _shallow_copy(params.parent_context.options)
        options_override.query_source = params.query_source

    # Build overrides
    overrides = SubagentContextOverrides(
        agent_id=agent_id,
        agent_type=agent_def.agent_type,
        teammate_name=params.agent_name,
        messages=sanitized_context_messages,
        abort_controller=abort_controller,
        permission_context=perm_context,
        share_abort_controller=not params.is_async,
        # Both sync and async subagents should contribute to response-length metrics.
        share_set_response_length=True,
        share_permission_handler=not params.is_async,
        options=options_override,
    )

    # Create isolated context
    subagent_context = create_subagent_context(
        params.parent_context,
        overrides,
    )

    # Build initial messages.
    # When ``params.prompt`` is empty (e.g. fork path, where the directive is
    # already embedded inside ``context_messages`` via build_forked_messages),
    # do not append an empty user turn — it would shift the cache boundary
    # and confuse the model with a blank input.
    if params.prompt:
        prompt_message = UserMessage(content=params.prompt)
        initial_messages: list[Message] = list(subagent_context.messages) + [prompt_message]
    else:
        initial_messages = list(subagent_context.messages)

    # Determine max turns — mirrors TS: maxTurns ?? agentDefinition.maxTurns
    # TS has no built-in fallback, but we keep a safety net to prevent runaway agents.
    max_turns = params.max_turns or agent_def.max_turns or SUBAGENT_DEFAULT_MAX_TURNS

    # ch08 round-4 WI-1 — per-subagent model resolution (TS getAgentModel,
    # runAgent.ts:340). Resolve the model from the tool param / agent-def /
    # env, then apply it to a per-subagent provider CLONE. NEVER mutate the
    # shared session provider: ch07 made Agent concurrency-safe, so N
    # parallel subagents share params.provider — mutating provider.model
    # would race across them. copy.copy shares the HTTP client (thread-safe,
    # per-request model) and gives this subagent its own .model.
    turn_provider = params.provider
    try:
        from .agent_model import get_agent_model

        resolved_model = get_agent_model(
            params.model, agent_def.model, params.provider,
        )
        if resolved_model and resolved_model != getattr(
            params.provider, "model", None
        ):
            turn_provider = copy.copy(params.provider)
            turn_provider.model = resolved_model
    except Exception:  # noqa: BLE001 — model resolution never blocks a spawn
        logger.debug("subagent model resolution failed; using session model",
                     exc_info=True)
        turn_provider = params.provider

    # --- Query loop ---
    # TS microcompact is a no-op for subagents (only fires for
    # repl_main_thread). Don't pass pipeline_config so we don't
    # aggressively clear tool results the model just read.
    # ch08 round-4 WI-2 — query_source labeling parity (agent:builtin:<type>
    # / agent:custom), TS promptCategory.getQuerySourceForAgent. The fork
    # path threads its own 'agent:builtin:fork' via params.query_source.
    effective_query_source = params.query_source or get_query_source_for_agent(
        agent_def.agent_type, is_built_in_agent(agent_def),
    )
    query_params = QueryParams(
        messages=initial_messages,
        system_prompt=system_prompt,
        tools=agent_tools,
        tool_registry=params.tool_registry,
        tool_use_context=subagent_context,
        provider=turn_provider,
        abort_controller=abort_controller,
        query_source=effective_query_source,
        max_turns=max_turns,
    )

    try:
        async for message in query(query_params):
            # Skip stream events — parent doesn't need them
            if isinstance(message, StreamEvent):
                continue

            agent_messages.append(message)

            if params.on_message:
                params.on_message(message)

            yield message

    except Exception as exc:
        logger.error("Agent %s (%s) failed: %s", agent_id, agent_def.agent_type, exc)
        raise
    finally:
        # Cleanup: release cloned file state cache memory
        subagent_context.read_file_fingerprints.clear()
        # Release initial messages
        initial_messages.clear()
        logger.debug(
            "Agent %s (%s) finished: %d messages, %d tool uses",
            agent_id,
            agent_def.agent_type,
            len(agent_messages),
            count_tool_uses(agent_messages),
        )
