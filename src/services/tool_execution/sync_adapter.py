"""Sync materialization adapter around ``run_tool_use``.

The agent loop in ``src/tool_system/agent_loop.py`` and the main
query loop's ``_dispatch_single_tool`` in ``src/query/query.py`` are
synchronous (the latter is dispatched onto a thread via
``asyncio.to_thread``). Both currently call
``ToolRegistry.dispatch()`` which implements only 6 of the 14
pipeline steps in ``ch06-tools.md`` — hooks, input backfill,
``new_messages`` injection, ``context_modifier`` propagation, and
telemetry-safe error classification are all silently skipped.

The fully-implemented pipeline lives in
``src/services/tool_execution/tool_execution.py:run_tool_use`` but
its API is an ``AsyncGenerator[MessageUpdateLazy, None]`` — a
mismatch with the sync, "return one ToolResult" call sites.

This adapter materializes the async generator into a single
``ToolDispatchResult`` dataclass with three side-channels surfaced
explicitly: ``new_messages`` (sub-agent transcripts, system reminders),
``context_modifier`` (mode changes like ``EnterPlanMode``), and the
raw ``output`` (preserved typed-dict / typed-str / typed-list shape so
sync callers branching on output shape continue to work).

The contract is:

- The FIRST tool_result block whose tool_use_id matches the call's id
  is treated as the primary result. ``output`` is read from the
  containing message's ``toolUseResult`` field (the run_tool_use
  pipeline stashes raw ``ToolResult.data`` there, except when
  ``agent_id`` is set — sub-agent path strips it, in which case we
  fall back to the block's serialized content).
- ``context_modifier`` is captured from any ``MessageUpdateLazy`` that
  carries one (the pipeline only attaches one per call).
- Everything else (pre-hook attachments, post-hook attachments,
  ``ToolResult.new_messages`` from inside ``tool.call``, and
  ``hook_stopped_continuation`` attachments) is bundled into
  ``new_messages``. Callers append them after the primary result.

The adapter is sync — it drives ``run_tool_use`` via ``asyncio.run``.
All current production callers run on a thread with no event loop
(``asyncio.to_thread`` for query.py; plain sync for agent_loop.py),
so this is correct. If a future caller invokes ``dispatch_full`` from
inside an event loop, that caller is responsible for using
``asyncio.run_in_executor`` or a thread-bridge.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from src.tool_system.build_tool import Tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolCall


@dataclass
class ToolDispatchResult:
    """Materialized result from one tool call dispatched through the
    full 13-step pipeline (``run_tool_use``).

    See module docstring for field semantics.
    """

    tool_result_block: dict[str, Any]
    """The ``{"type": "tool_result", "tool_use_id": ..., "content": ...,
    "is_error": ...}`` block ready for inclusion in a UserMessage.
    Already mapped + budgeted by Step 11 of the pipeline."""

    is_error: bool
    """Mirror of ``tool_result_block["is_error"]``."""

    output: Any
    """The raw ``ToolResult.data`` from the tool's ``call()``. Preserves
    typed shape (dict / str / list) on success.

    On error paths (validation failure, permission denial, tool
    exception) ``output`` carries the pipeline-synthesized error
    string (e.g. ``"Error: No way"``), NOT ``None`` — the
    ``toolUseResult`` field that the adapter sources from is populated
    even on short-circuit paths. **Branch on ``is_error`` BEFORE
    reading ``output`` to distinguish a real None return from a
    pipeline error.**

    Sub-agent dispatches (``context.agent_id`` set) strip
    ``toolUseResult`` to None; the adapter then falls back to the
    serialized block content as a best-effort string."""

    new_messages: list[Any] = field(default_factory=list)
    """Auxiliary messages the caller must append to the conversation
    after the primary tool result. Includes pre-hook attachments,
    post-hook attachments, ``ToolResult.new_messages`` (sub-agent
    transcripts, system reminders), and ``hook_stopped_continuation``
    attachments."""

    context_modifier: Callable[[ToolContext], ToolContext] | None = None
    """Function returned by tools like ``EnterPlanMode`` that mutates
    the ``ToolContext`` for subsequent tools in the same turn. Callers
    are responsible for applying it (and for ordering: serial batches
    apply immediately; concurrent batches queue until the batch ends)."""


def _default_can_use_tool(
    tool: Tool,
    tool_input: dict[str, Any],
    tool_use_context: ToolContext,
    _assistant_message: Any,
    _tool_use_id: str,
    _force_decision: Any = None,
) -> dict[str, Any]:
    """Default permission resolver used when the caller doesn't supply
    a ``can_use_tool`` callback.

    Wraps ``has_permissions_to_use_tool`` from the permissions module so
    the tool's own ``check_permissions`` method (and rule-based deny
    checks) actually run. Without this default the pipeline's
    ``resolve_hook_permission_decision`` falls through to "allow"
    whenever ``can_use_tool`` is None, which makes tool-level deny
    decisions silently ineffective.

    ``ask`` decisions are resolved interactively via
    ``handle_permission_ask`` (consulting
    ``tool_use_context.permission_handler`` when set) so the adapter
    matches the existing ``ToolRegistry.dispatch()`` semantics. When no
    permission handler is registered, ``handle_permission_ask`` returns
    a deny — same as ``dispatch()`` does today.

    Returns the decision in the dict shape the pipeline expects:
    ``{"behavior": "allow"|"deny", "message": str | None,
       "updatedInput": dict | None}`` (``ask`` is always resolved here,
    not propagated up).
    """
    from src.permissions.check import has_permissions_to_use_tool
    from src.permissions.handler import handle_permission_ask
    from src.permissions.types import PermissionAskDecision

    decision = has_permissions_to_use_tool(
        tool, tool_input, tool_use_context.permission_context,
        tool_use_context=tool_use_context,
    )

    if isinstance(decision, PermissionAskDecision):
        handler_cb = None
        permission_handler = getattr(tool_use_context, "permission_handler", None)
        if permission_handler is not None:
            raw_handler = permission_handler

            def _adapted_handler(
                tn: str, msg: str, suggestions: Any,
            ) -> tuple[bool, dict[str, Any] | None]:
                allowed, _ = raw_handler(tn, msg, None)
                return allowed, None

            handler_cb = _adapted_handler
        decision = handle_permission_ask(tool.name, decision, handler_cb)

    payload: dict[str, Any] = {"behavior": getattr(decision, "behavior", "allow")}
    msg = getattr(decision, "message", None)
    if msg:
        payload["message"] = msg
    updated = getattr(decision, "updated_input", None)
    if updated is not None:
        payload["updatedInput"] = updated
    return payload


def dispatch_full(
    tool_call: ToolCall,
    tool_use_context: ToolContext,
    assistant_message: Any,
    *,
    tools: list[Tool] | None = None,
    can_use_tool: Any = None,
) -> ToolDispatchResult:
    """Sync wrapper that drives ``run_tool_use`` to completion.

    Args:
        tool_call: the call to dispatch (name + input + tool_use_id).
        tool_use_context: the ``ToolContext`` the pipeline operates on.
            Mutations made by hooks/budget tracking apply in place to
            this context (the running aggregate counter, the
            ``tool_use_id`` field, etc.).
        assistant_message: the originating ``AssistantMessage`` that
            emitted the tool call. Hooks have access to this. Callers
            who don't have a real assistant message can construct a
            stub via ``_make_stub_assistant_message`` (provided in
            this module).
        tools: explicit tool list for name lookup. Overrides
            ``tool_use_context.options.tools`` when provided.
        can_use_tool: optional ``CanUseToolFn`` hook for permission
            override; ``None`` lets the pipeline use the default
            ``has_permissions_to_use_tool`` flow.

    Returns:
        A ``ToolDispatchResult`` with the primary block, raw output,
        any new messages, and the optional context modifier.

    Raises:
        Nothing from ``tool.call`` directly — exceptions inside the
        pipeline are caught and surfaced as ``is_error=True`` results.
        ``asyncio.run`` will surface a ``RuntimeError`` if called from
        inside a running event loop — production callers are sync /
        on worker threads, so this should not occur.
    """
    from src.services.tool_execution.streaming_executor import ToolUseBlock
    from src.services.tool_execution.tool_execution import run_tool_use

    tool_use_block = ToolUseBlock(
        id=tool_call.tool_use_id or "",
        name=tool_call.name,
        input=tool_call.input,
    )

    # Provide a default permission resolver when the caller doesn't,
    # otherwise the pipeline silently auto-allows every tool (see
    # ``_default_can_use_tool`` docstring for context).
    effective_can_use_tool = can_use_tool if can_use_tool is not None else _default_can_use_tool

    async def _run() -> ToolDispatchResult:
        primary_block: dict[str, Any] | None = None
        is_error = False
        output: Any = None
        new_messages: list[Any] = []
        context_modifier: Callable[[ToolContext], ToolContext] | None = None

        async for update in run_tool_use(
            tool_use_block,
            assistant_message,
            effective_can_use_tool,
            tool_use_context,
            tools=tools,
        ):
            msg = _msg_of(update)
            ctx_mod = _ctx_mod_of(update)

            if ctx_mod is not None and context_modifier is None:
                context_modifier = ctx_mod

            if msg is None:
                continue

            block = _primary_tool_result_block_for(msg, tool_call.tool_use_id)
            if primary_block is None and block is not None:
                primary_block = block
                is_error = bool(block.get("is_error"))
                # Prefer the raw ``ToolResult.data`` (stashed on
                # ``toolUseResult``) so callers branching on dict/str
                # shape keep working. When ``agent_id`` is set the
                # pipeline strips this field — fall back to the
                # serialized block content.
                tur = getattr(msg, "toolUseResult", None)
                output = tur if tur is not None else block.get("content")
                continue

            # Everything else is an auxiliary message — pre-hook
            # attachment, post-hook attachment, ToolResult.new_messages,
            # or a hook_stopped_continuation attachment. Caller appends
            # these after the primary result.
            new_messages.append(msg)

        if primary_block is None:
            # Pipeline yielded no tool_result block matching the call —
            # synthesize an error block so the model gets a matched
            # tool_use_id (otherwise the next API turn 400s on an
            # unmatched tool_use).
            primary_block = {
                "type": "tool_result",
                "tool_use_id": tool_call.tool_use_id or "",
                "content": "<tool_use_error>No result yielded by pipeline</tool_use_error>",
                "is_error": True,
            }
            is_error = True

        return ToolDispatchResult(
            tool_result_block=primary_block,
            is_error=is_error,
            output=output,
            new_messages=new_messages,
            context_modifier=context_modifier,
        )

    return asyncio.run(_run())


def make_stub_assistant_message(uuid: str | None = None) -> Any:
    """Construct a minimal ``AssistantMessage`` for callers that don't
    have a real one.

    Hooks may behave differently when handed a stub — they read
    ``.content``, ``.uuid``, and occasionally ``.usage``. Slash-command
    dispatches (no model in the loop) typically don't have hooks
    configured that look at these fields, so the stub is fine. Callers
    invoking the agent loop or query loop SHOULD pass the real
    assistant message in scope.

    Passing ``uuid=None`` (the default) generates a fresh uuid4 via
    AssistantMessage's default_factory. Pass an explicit empty string
    only if you want a deliberately-empty uuid (rarely useful).
    """
    from src.types.messages import AssistantMessage
    if uuid is None:
        return AssistantMessage(content=[])
    return AssistantMessage(content=[], uuid=uuid)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _msg_of(update: Any) -> Any:
    """Extract the ``.message`` from a ``MessageUpdateLazy`` (object or
    dict shape)."""
    if isinstance(update, dict):
        return update.get("message")
    return getattr(update, "message", None)


def _ctx_mod_of(update: Any) -> Callable[[ToolContext], ToolContext] | None:
    """Extract the ``modify_context`` function from a
    ``MessageUpdateLazy.context_modifier`` (object or dict shape)."""
    if isinstance(update, dict):
        ctx_mod = update.get("context_modifier")
    else:
        ctx_mod = getattr(update, "context_modifier", None)
    if ctx_mod is None:
        return None
    if isinstance(ctx_mod, dict):
        return ctx_mod.get("modify_context")
    return getattr(ctx_mod, "modify_context", None)


def _primary_tool_result_block_for(msg: Any, tool_use_id: str | None) -> dict[str, Any] | None:
    """Return the first ``tool_result`` content block in ``msg`` whose
    ``tool_use_id`` matches ``tool_use_id``. None if not found.

    The pipeline's first yielded message contains the primary result;
    we still match by tool_use_id to be robust against ordering changes.
    """
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            if tool_use_id is None or block.get("tool_use_id") == tool_use_id:
                return block
    return None
