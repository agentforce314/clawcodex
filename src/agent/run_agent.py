"""Agent lifecycle management.

Mirrors typescript/src/tools/AgentTool/runAgent.ts and forkedAgent.ts.
Provides the core run_agent() async generator and filter_incomplete_tool_calls().
"""
from __future__ import annotations

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
from ..utils.abort_controller import AbortController, create_child_abort_controller

from .agent_definitions import AgentDefinition, is_built_in_agent
from .agent_tool_utils import resolve_agent_tools, count_tool_uses
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
    is_async: bool = False
    max_turns: int | None = None
    system_prompt_override: str | None = None
    parent_system_prompt: str | None = None
    permission_mode_override: PermissionMode | None = None
    context_messages: list[Message] | None = None
    abort_controller: AbortController | None = None
    on_message: Any = None


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
    """Build the permission context for the subagent."""
    parent_perm = parent_context.permission_context
    return ToolPermissionContext(
        mode=effective_mode,
        additional_working_directories=parent_perm.additional_working_directories,
        always_allow_rules=parent_perm.always_allow_rules,
        always_deny_rules=parent_perm.always_deny_rules,
        always_ask_rules=parent_perm.always_ask_rules,
        is_bypass_permissions_mode_available=parent_perm.is_bypass_permissions_mode_available,
        should_avoid_permission_prompts=(
            parent_perm.should_avoid_permission_prompts or is_async
        ),
    )


def filter_incomplete_tool_calls(messages: list[Message]) -> list[Message]:
    """Remove trailing assistant messages that have incomplete tool_use blocks.

    Mirrors filterIncompleteToolCalls() from typescript/src/tools/AgentTool/runAgent.ts.

    When an agent is interrupted, the last assistant message may contain tool_use
    blocks without corresponding tool_result messages. Sending these would cause
    an API error. This function removes such trailing messages.
    """
    if not messages:
        return messages

    result = list(messages)
    while result:
        last = result[-1]
        if not isinstance(last, AssistantMessage):
            break
        content = last.content
        if not isinstance(content, list):
            break
        has_tool_use = any(
            isinstance(block, ToolUseBlock) for block in content
        )
        if not has_tool_use:
            break
        # Check if there's a following user message with tool_result
        result.pop()

    return result


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

    # Resolve permission mode
    effective_mode = resolve_permission_mode(
        params.parent_context,
        agent_def,
        is_async=params.is_async,
    )

    # Resolve tools for the agent
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

    # Determine abort controller
    if params.abort_controller is not None:
        abort_controller = params.abort_controller
    elif params.is_async and params.parent_context.abort_controller is not None:
        # Async agents get an independent abort controller (child linked to parent)
        abort_controller = create_child_abort_controller(
            params.parent_context.abort_controller
        )
    elif params.parent_context.abort_controller is not None:
        # Sync agents share abort with parent
        abort_controller = params.parent_context.abort_controller
    else:
        abort_controller = AbortController()

    # Build permission context
    perm_context = _build_permission_context(
        params.parent_context,
        effective_mode,
        params.is_async,
    )

    # Build overrides
    overrides = SubagentContextOverrides(
        agent_id=agent_id,
        agent_type=agent_def.agent_type,
        messages=params.context_messages or [],
        abort_controller=abort_controller,
        permission_context=perm_context,
        share_abort_controller=not params.is_async,
        share_set_response_length=not params.is_async,
        share_permission_handler=not params.is_async,
    )

    # Create isolated context
    subagent_context = create_subagent_context(
        params.parent_context,
        overrides,
    )

    # Build initial messages
    prompt_message = UserMessage(content=params.prompt)
    initial_messages: list[Message] = list(subagent_context.messages) + [prompt_message]

    # Determine max turns — mirrors TS: maxTurns ?? agentDefinition.maxTurns
    # TS has no built-in fallback, but we keep a safety net to prevent runaway agents.
    max_turns = params.max_turns or agent_def.max_turns or SUBAGENT_DEFAULT_MAX_TURNS

    # --- Query loop ---
    # TS microcompact is a no-op for subagents (only fires for
    # repl_main_thread). Don't pass pipeline_config so we don't
    # aggressively clear tool results the model just read.
    query_params = QueryParams(
        messages=initial_messages,
        system_prompt=system_prompt,
        tools=agent_tools,
        tool_registry=params.tool_registry,
        tool_use_context=subagent_context,
        provider=params.provider,
        abort_controller=abort_controller,
        query_source=f"agent_{agent_def.agent_type}",
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
