"""Subagent context creation and isolation.

Mirrors createSubagentContext() from typescript/src/utils/forkedAgent.ts.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ..permissions.types import ToolPermissionContext
from ..tool_system.context import (
    FileReadingLimits,
    QueryChainTracking,
    ToolContext,
    ToolUseOptions,
)
from ..utils.abort_controller import AbortController, create_child_abort_controller

logger = logging.getLogger(__name__)


@dataclass
class SubagentContextOverrides:
    """Options for creating an isolated subagent context.

    By default, all mutable state is isolated to prevent interference with
    the parent. Use these options to override specific fields or opt-in to
    sharing specific callbacks.
    """
    # Override fields
    options: ToolUseOptions | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    messages: list[Any] | None = None
    read_file_state: dict[Any, Any] | None = None
    abort_controller: AbortController | None = None
    permission_context: ToolPermissionContext | None = None

    # Opt-in sharing flags
    share_abort_controller: bool = False
    share_set_response_length: bool = False
    share_permission_handler: bool = False

    # Content replacement state override
    content_replacement_state: Any | None = None

    # Critical system reminder for every user turn
    critical_system_reminder: str | None = None


def create_subagent_context(
    parent_context: ToolContext,
    overrides: SubagentContextOverrides | None = None,
) -> ToolContext:
    """Create an isolated ToolContext for subagents.

    Mirrors createSubagentContext() from typescript/src/utils/forkedAgent.ts.

    By default, ALL mutable state is isolated to prevent interference:
    - read_file_fingerprints: cloned from parent
    - abort_controller: new controller linked to parent (parent abort propagates)
    - permission_context: wrapped to set should_avoid_permission_prompts
    - Mutation callbacks: no-op
    - Fresh collections: todos, tasks, outbox

    Callers can:
    - Override specific fields via the overrides parameter
    - Explicitly opt-in to sharing specific callbacks
    """
    if overrides is None:
        overrides = SubagentContextOverrides()

    # --- Abort controller ---
    # Priority: explicit override > share parent's > new child linked to parent
    if overrides.abort_controller is not None:
        abort_controller = overrides.abort_controller
    elif overrides.share_abort_controller and parent_context.abort_controller is not None:
        abort_controller = parent_context.abort_controller
    elif parent_context.abort_controller is not None:
        abort_controller = create_child_abort_controller(parent_context.abort_controller)
    else:
        abort_controller = AbortController()

    # --- Permission context ---
    # If sharing abort controller, it's interactive and can show UI.
    # Otherwise, set should_avoid_permission_prompts.
    if overrides.permission_context is not None:
        permission_context = overrides.permission_context
    elif overrides.share_abort_controller:
        # Interactive agent — share parent's permission context
        permission_context = parent_context.permission_context
    else:
        # Background agent — suppress permission prompts
        permission_context = _wrap_avoid_prompts(parent_context.permission_context)

    # --- Read file state (fingerprints) ---
    # TS behaviour: subagents start with an EMPTY fingerprint cache.
    # They have NOT read any files yet, so inheriting the parent's cache
    # causes the Read tool to return "file_unchanged" for files the
    # subagent has never seen, forcing wasteful Bash fallbacks.
    if overrides.read_file_state is not None:
        read_file_fingerprints = dict(overrides.read_file_state)
    else:
        read_file_fingerprints: dict[Any, Any] = {}

    # --- Options ---
    options = overrides.options if overrides.options is not None else parent_context.options

    # --- Messages ---
    messages = overrides.messages if overrides.messages is not None else list(parent_context.messages)

    # --- Query tracking with incremented depth ---
    parent_depth = parent_context.query_tracking.depth if parent_context.query_tracking else -1
    query_tracking = QueryChainTracking(
        chain_id=uuid4().hex,
        depth=parent_depth + 1,
    )

    # --- Agent ID ---
    agent_id = overrides.agent_id if overrides.agent_id is not None else uuid4().hex

    # --- Agent type ---
    agent_type = overrides.agent_type

    # --- Permission handler ---
    # Only share if explicitly opted in; otherwise no-op (None)
    permission_handler = (
        parent_context.permission_handler
        if overrides.share_permission_handler
        else None
    )

    # --- Set response length ---
    set_response_length = (
        parent_context.set_response_length
        if overrides.share_set_response_length
        else None
    )

    # --- Content replacement state ---
    if overrides.content_replacement_state is not None:
        content_replacement_state = overrides.content_replacement_state
    elif parent_context.content_replacement_state is not None:
        # Clone by default for prompt cache stability
        content_replacement_state = copy.deepcopy(parent_context.content_replacement_state)
    else:
        content_replacement_state = None

    # --- Build the isolated context ---
    return ToolContext(
        workspace_root=parent_context.workspace_root,
        permission_context=permission_context,
        cwd=parent_context.cwd,
        read_file_fingerprints=read_file_fingerprints,
        task_manager=parent_context.task_manager,
        mcp_clients=parent_context.mcp_clients,
        lsp_client=parent_context.lsp_client,
        # Fresh isolated collections
        todos=[],
        tasks={},
        outbox=[],
        crons={},
        # No-op / None for UI callbacks
        ask_user=None,
        team=parent_context.team,
        output_style_name=parent_context.output_style_name,
        output_style_dir=parent_context.output_style_dir,
        additional_working_directories=parent_context.additional_working_directories,
        allow_docs=parent_context.allow_docs,
        permission_handler=permission_handler,
        options=options,
        abort_controller=abort_controller,
        messages=messages,
        set_response_length=set_response_length,
        set_in_progress_tool_use_ids=None,
        query_tracking=query_tracking,
        file_reading_limits=parent_context.file_reading_limits,
        glob_limits=parent_context.glob_limits,
        content_replacement_state=content_replacement_state,
        agent_id=agent_id,
        agent_type=agent_type,
        user_modified=parent_context.user_modified,
    )


def _wrap_avoid_prompts(ctx: ToolPermissionContext) -> ToolPermissionContext:
    """Return a permission context with should_avoid_permission_prompts set."""
    if ctx.should_avoid_permission_prompts:
        return ctx
    return ToolPermissionContext(
        mode=ctx.mode,
        additional_working_directories=ctx.additional_working_directories,
        always_allow_rules=ctx.always_allow_rules,
        always_deny_rules=ctx.always_deny_rules,
        always_ask_rules=ctx.always_ask_rules,
        is_bypass_permissions_mode_available=ctx.is_bypass_permissions_mode_available,
        should_avoid_permission_prompts=True,
    )
