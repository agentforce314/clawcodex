from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable
from uuid import uuid4

from ..types.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    UserMessage,
)
from ..types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from ..tool_system.build_tool import Tool, Tools, find_tool_by_name
from ..tool_system.context import ToolContext
from ..tool_system.protocol import ToolCall, ToolResult
from ..tool_system.registry import ToolRegistry
from ..utils.abort_controller import AbortController
from ..providers.base import BaseProvider, ChatResponse

from .config import QueryConfig, build_query_config
from .transitions import QueryState, Terminal, Transition
from ..services.compact.pipeline import (
    CompressionPipeline,
    PipelineConfig,
    run_compression_pipeline,
)
from ..token_estimation import rough_token_count_estimation_for_messages

logger = logging.getLogger(__name__)

ESCALATED_MAX_TOKENS = 64_000
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3
PROMPT_TOO_LONG_ERROR_MESSAGE = (
    "Your conversation is too long. Please use /compact to reduce context size, "
    "or start a new conversation."
)


@dataclass
class QueryParams:
    messages: list[Message]
    # WI-1.1: ``system_prompt`` accepts either the legacy ``str`` shape
    # (joined sections, no cache_control markers) OR the block-list shape
    # ``list[dict]`` produced by ``build_full_system_prompt_blocks``. The
    # block-list shape is what engages Anthropic's prompt cache via
    # ``cache_control: {type: 'ephemeral'}`` markers; the str shape is
    # retained for backward compat with callers that pass a custom prompt.
    system_prompt: str | list[dict[str, Any]]
    tools: Tools
    tool_registry: ToolRegistry
    tool_use_context: ToolContext
    provider: BaseProvider
    abort_controller: AbortController
    query_source: str = "repl_main_thread"
    max_output_tokens_override: int | None = None
    max_turns: int | None = None
    user_context: dict[str, str] | None = None
    system_context: dict[str, str] | None = None
    pipeline_config: PipelineConfig | None = None


@dataclass
class StreamEvent:
    type: str
    data: Any = None


def _is_prompt_too_long_message(msg: Message) -> bool:
    if not isinstance(msg, AssistantMessage):
        return False
    if not hasattr(msg, "_api_error"):
        return False
    return getattr(msg, "_api_error", None) == "prompt_too_long"


def _create_user_message(content: str, *, is_meta: bool = False) -> UserMessage:
    return UserMessage(
        content=content,
        isMeta=is_meta,
    )


def _create_assistant_api_error_message(
    content: str,
    *,
    error: str | None = None,
) -> AssistantMessage:
    msg = AssistantMessage(content=content, isApiErrorMessage=True)
    msg._api_error = error  # type: ignore[attr-defined]
    return msg


def _create_user_interruption_message(*, tool_use: bool = False) -> UserMessage:
    from ..types.messages import INTERRUPT_MESSAGE, INTERRUPT_MESSAGE_FOR_TOOL_USE
    content = INTERRUPT_MESSAGE_FOR_TOOL_USE if tool_use else INTERRUPT_MESSAGE
    return UserMessage(content=content, isMeta=True)


def _create_max_turns_attachment(max_turns: int, turn_count: int) -> SystemMessage:
    return SystemMessage(
        content=f"Reached maximum number of turns ({max_turns})",
        subtype="max_turns_reached",
    )


def _drain_pending_user_messages(tool_use_context: Any) -> list[UserMessage]:
    """Drain the running agent's ``pending_messages`` inbox, if any.

    Chapter-10 / Chunk D / WI-3.3 hook. The TS implementation drains at
    the tool-round boundary inside the agent's run loop; the Python
    equivalent is here, between `tool_results` and the next API call,
    where the chapter's "messages arrive between tool rounds, not
    mid-execution" contract holds.

    No-op when:
    * The context has no ``agent_id`` (top-level / non-runtime-task agents).
    * The context has no ``runtime_tasks`` registry (test fixtures
      that didn't construct a real ToolContext).
    * The agent's entry isn't a ``LocalAgentTaskState`` (defensive —
      a future task type that runs through the same query loop).
    * The inbox is empty.

    Returns the drained messages as a list of fresh ``UserMessage``
    objects, which the caller appends to the next turn's prompt.
    """
    agent_id = getattr(tool_use_context, "agent_id", None)
    runtime = getattr(tool_use_context, "runtime_tasks", None)
    if not agent_id or runtime is None:
        return []
    # Local import to avoid pulling the tasks package into the query
    # module's import graph at startup; this hook only fires when an
    # agent_id is set, so the tasks module will already be loaded.
    try:
        from src.tasks.local_agent import (
            LocalAgentTaskState,
            drain_pending_messages,
        )
    except ImportError:
        return []
    state = runtime.get(agent_id)
    if not isinstance(state, LocalAgentTaskState):
        return []
    drained = drain_pending_messages(agent_id, runtime)
    if not drained:
        return []
    return [UserMessage(content=text) for text in drained]


def _yield_missing_tool_result_blocks(
    assistant_messages: list[AssistantMessage],
    error_message: str,
) -> list[UserMessage]:
    results: list[UserMessage] = []
    for assistant_msg in assistant_messages:
        content = assistant_msg.content
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, ToolUseBlock):
                results.append(
                    UserMessage(
                        content=[
                            ToolResultBlock(
                                tool_use_id=block.id,
                                content=error_message,
                                is_error=True,
                            )
                        ],
                    )
                )
    return results


def _is_withheld_max_output_tokens(msg: Message | None) -> bool:
    if msg is None:
        return False
    if not isinstance(msg, AssistantMessage):
        return False
    return getattr(msg, "_api_error", None) == "max_output_tokens"


async def _call_model_sync(
    *,
    provider: BaseProvider,
    messages: list[Message],
    system_prompt: str,
    tools: Tools,
    max_output_tokens_override: int | None = None,
) -> tuple[list[AssistantMessage], list[ToolUseBlock]]:
    from ..types.messages import normalize_messages_for_api

    api_messages = normalize_messages_for_api(messages)

    # --- Diagnostic tracing ---
    _diag = os.environ.get("CLAWCODEX_DEBUG", "").lower() in ("1", "true", "yes")
    if _diag:
        _total_chars = sum(
            len(m.get("content", "")) if isinstance(m.get("content"), str)
            else sum(len(str(b)) for b in m.get("content", []))
            for m in api_messages
        )
        if isinstance(system_prompt, str):
            sys_desc = f"{len(system_prompt)} chars"
        else:
            sys_total_chars = sum(
                len(blk.get("text", ""))
                for blk in system_prompt
                if isinstance(blk, dict)
            )
            sys_desc = f"{len(system_prompt)} blocks, {sys_total_chars} chars"
        logger.warning(
            "[DIAG] _call_model_sync: %d api_messages, ~%d chars, system_prompt=%s, %d tools",
            len(api_messages), _total_chars, sys_desc, len(list(tools)),
        )
        for i, m in enumerate(api_messages):
            role = m.get("role", "?")
            c = m.get("content", "")
            if isinstance(c, str):
                clen = len(c)
                logger.warning("[DIAG]   msg[%d] role=%s  content_len=%d  text=%s", i, role, clen, c[:80])
            else:
                block_types = []
                for b in c:
                    if isinstance(b, dict):
                        bt = b.get("type", "?")
                        if bt == "tool_use":
                            block_types.append(f"tool_use(id={b.get('id','')},name={b.get('name','')})")
                        elif bt == "tool_result":
                            block_types.append(f"tool_result(tool_use_id={b.get('tool_use_id','')})")
                        else:
                            block_types.append(bt)
                    else:
                        block_types.append(str(type(b).__name__))
                logger.warning("[DIAG]   msg[%d] role=%s  blocks=%s", i, role, block_types)
    _t0 = time.monotonic()
    tool_schemas = []
    for tool in tools:
        tool_schemas.append({
            "name": tool.name,
            "description": tool.prompt(),
            "input_schema": dict(tool.input_schema),
        })

    call_kwargs: dict[str, Any] = {"tools": tool_schemas}

    from ..providers.anthropic_provider import AnthropicProvider
    from ..providers.minimax_provider import MinimaxProvider

    is_anthropic = isinstance(provider, (AnthropicProvider, MinimaxProvider))
    if is_anthropic:
        # Forward whatever shape the engine produced — str or list[dict].
        # The SDK's ``system`` param accepts ``Union[str, Iterable[TextBlockParam]]``;
        # cache_control markers on blocks engage server-side prompt caching.
        call_kwargs["system"] = system_prompt
    else:
        # Non-Anthropic providers (OpenAI-compat, GLM, etc.) consume the
        # system prompt as a single string injected as a ``system`` message.
        # Flatten the block-list shape to a string by concatenating block text;
        # cache_control markers don't apply to these providers anyway.
        #
        # Critically, FILTER OUT the dynamic-boundary marker block. The
        # literal ``__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`` is a cache-only
        # signal for the Anthropic backend; emitting it as raw text into
        # a non-Anthropic system prompt embeds an unintelligible token in
        # the prose that may confuse those models.
        if isinstance(system_prompt, list):
            from ..context_system.cache_boundary import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            flattened = "\n\n".join(
                str(blk.get("text", ""))
                for blk in system_prompt
                if isinstance(blk, dict)
                and blk.get("text")
                and blk.get("text") != SYSTEM_PROMPT_DYNAMIC_BOUNDARY
            )
        else:
            flattened = system_prompt
        api_messages = [{"role": "system", "content": flattened}, *api_messages]

    if max_output_tokens_override is not None:
        call_kwargs["max_tokens"] = max_output_tokens_override

    # TS callModel() uses SSE streaming for faster first-byte latency and
    # progressive text display.  Use chat_stream_response() which streams
    # internally and reassembles the full ChatResponse.  Fall back to the
    # synchronous chat() if the provider doesn't support structured streaming.
    if _diag:
        logger.warning("[DIAG] _call_model_sync: calling provider (streaming)...")
    try:
        try:
            response = provider.chat_stream_response(api_messages, **call_kwargs)
        except (NotImplementedError, AttributeError):
            if _diag:
                logger.warning("[DIAG] _call_model_sync: streaming not supported, falling back to chat()")
            response = provider.chat(api_messages, **call_kwargs)
    except Exception as e:
        if _diag:
            logger.warning("[DIAG] _call_model_sync: EXCEPTION after %.1fs: %s", time.monotonic() - _t0, e)
        error_str = str(e)
        if "prompt is too long" in error_str.lower() or "prompt_too_long" in error_str.lower():
            err_msg = _create_assistant_api_error_message(
                PROMPT_TOO_LONG_ERROR_MESSAGE,
                error="prompt_too_long",
            )
            err_msg._api_error = "prompt_too_long"  # type: ignore[attr-defined]
            return [err_msg], []

        if "max_tokens" in error_str.lower() or "max_output_tokens" in error_str.lower():
            err_msg = _create_assistant_api_error_message(
                "Output token limit reached.",
                error="max_output_tokens",
            )
            err_msg._api_error = "max_output_tokens"  # type: ignore[attr-defined]
            return [err_msg], []

        raise

    assistant_blocks: list[Any] = []
    tool_use_blocks: list[ToolUseBlock] = []

    if response.content:
        assistant_blocks.append(TextBlock(text=response.content))

    if response.tool_uses:
        for tu in response.tool_uses:
            block = ToolUseBlock(
                id=tu["id"],
                name=tu["name"],
                input=tu["input"],
            )
            assistant_blocks.append(block)
            tool_use_blocks.append(block)

    stop_reason = response.finish_reason or "end_turn"

    if _diag:
        _elapsed = time.monotonic() - _t0
        _text_len = len(response.content) if response.content else 0
        _tool_count = len(response.tool_uses) if response.tool_uses else 0
        logger.warning(
            "[DIAG] _call_model_sync: response in %.1fs  text=%d chars  tools=%d  finish=%s  usage=%s",
            _elapsed, _text_len, _tool_count, stop_reason, response.usage,
        )

    assistant_msg = AssistantMessage(
        content=assistant_blocks if assistant_blocks else "",
        stop_reason=stop_reason,
        usage=response.usage,
    )
    if response.reasoning_content:
        # Preserve provider thinking metadata for follow-up turns.
        assistant_msg.reasoning_content = response.reasoning_content  # type: ignore[attr-defined]

    if stop_reason == "max_tokens":
        assistant_msg._api_error = "max_output_tokens"  # type: ignore[attr-defined]
        assistant_msg.isApiErrorMessage = False

    return [assistant_msg], tool_use_blocks


# Max tools to run in parallel (TS default: 10, configurable via env var).
# Reads CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY (the name documented in
# chapter 7 and used in the TS reference). Falls back to the legacy
# CLAWCODEX_MAX_TOOL_USE_CONCURRENCY with a DeprecationWarning so
# users learn to migrate.
def _resolve_max_tool_use_concurrency() -> int:
    canonical = os.environ.get("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY")
    if canonical is not None:
        return int(canonical)
    legacy = os.environ.get("CLAWCODEX_MAX_TOOL_USE_CONCURRENCY")
    if legacy is not None:
        import warnings
        warnings.warn(
            "CLAWCODEX_MAX_TOOL_USE_CONCURRENCY is deprecated; use "
            "CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return int(legacy)
    return 10


MAX_TOOL_USE_CONCURRENCY = _resolve_max_tool_use_concurrency()


@dataclass
class _ToolBatch:
    """A batch of tool_use blocks with the same concurrency classification."""
    is_concurrent_safe: bool
    blocks: list[ToolUseBlock]


def _partition_tool_calls(
    tool_use_blocks: list[ToolUseBlock],
    tools: Tools,
) -> list[_ToolBatch]:
    """**DEPRECATED** — ch07 / Phase 2e production cutover replaced this with
    ``orchestrator.partition_tool_calls`` (which takes a ``ToolContext`` rather
    than a ``Tools`` list). Retained only because
    ``tests/test_tool_result_budget.py`` imports the surrounding
    ``_dispatch_single_tool`` shim; remove this and the rest of the legacy
    `_run_tools_partitioned` family after that test file is rewritten to
    drive ``run_tool_use`` directly (Option A in the refactoring plan).

    Partition tool calls into batches per TS partitionToolCalls().
    Consecutive ConcurrencySafe tools are grouped for parallel execution.
    Non-safe tools each get their own exclusive batch.
    """
    batches: list[_ToolBatch] = []
    for block in tool_use_blocks:
        tool = find_tool_by_name(tools, block.name)
        try:
            is_safe = bool(tool.is_concurrency_safe(block.input)) if tool else False
        except Exception:
            is_safe = False
        if batches and is_safe and batches[-1].is_concurrent_safe:
            batches[-1].blocks.append(block)
        else:
            batches.append(_ToolBatch(is_concurrent_safe=is_safe, blocks=[block]))
    return batches


def _dispatch_single_tool(
    block: ToolUseBlock,
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
    tools: Tools | None = None,
) -> UserMessage:
    """**DEPRECATED** — ch07 / Phase 2e cutover routed production through
    ``_collect_tool_results`` → ``orchestrator.run_tools`` → ``run_tool_use``
    (which honours PreToolUse/PostToolUse hooks). This shim is retained
    only for ``tests/test_tool_result_budget.py`` callers; **no
    production path invokes it**. Remove this and the rest of the legacy
    ``_run_tools_partitioned`` family after that test file is rewritten
    to drive ``run_tool_use`` directly.

    The inline lock + counter logic below is duplicated in
    ``tool_execution.py`` (Phase 2b); both writes target the same shared
    ``aggregate_budget.total`` via the property setter (Phase 2a.5), so
    they don't double-count when only one path is live at a time.

    Routes through ``process_tool_result_block`` (mirrors TS Step 11 of
    the execution pipeline at ``processToolResultBlock``) so the per-tool
    persistence threshold AND the WI-5.1 per-message aggregate budget
    both engage on the production path. The running aggregate is held on
    ``tool_use_context.tool_result_chars_so_far`` (reset at the top of
    each per-turn loop in :func:`query`).
    """
    try:
        call = ToolCall(
            name=block.name,
            input=block.input,
            tool_use_id=block.id,
        )
        result = tool_registry.dispatch(call, tool_use_context)

        tool = find_tool_by_name(tools, block.name) if tools else None
        metadata: dict[str, Any] = {}
        if isinstance(result.output, dict):
            metadata["tool_output"] = result.output

        if tool is not None:
            # WI-5.1: route through ``process_tool_result_block`` so the
            # 200K per-message aggregate cap is enforced. Without this
            # call the production REPL ran ``map_result_to_api`` directly
            # and never engaged the gate (critic B2).
            #
            # The read-decide-write on ``tool_result_chars_so_far`` is
            # serialized via ``_aggregate_lock`` because ``_run_tools_partitioned``
            # dispatches concurrency-safe tools (Read/Grep/Glob) via
            # ``asyncio.to_thread`` (critic B6). The decision MUST be
            # made on a fresh snapshot of the counter — otherwise N
            # threads racing the read all see 0, all decide "under cap"
            # and the cap is silently bypassed. ``process_tool_result_block``
            # is called inside the critical section: for small blocks
            # under threshold it just returns the block (no I/O); the
            # rare persist-to-disk path runs while serialized but those
            # are at most O(1) per turn (typically <5%).
            from ..services.tool_execution.tool_result_persistence import (
                compute_block_chars,
                process_tool_result_block,
                resolve_tool_results_dir,
            )
            tool_results_dir = resolve_tool_results_dir(tool_use_context)
            with tool_use_context._aggregate_lock:
                aggregate_so_far = tool_use_context.tool_result_chars_so_far
                api_block = process_tool_result_block(
                    tool,
                    result.output,
                    block.id,
                    tool_results_dir=tool_results_dir,
                    aggregate_chars_so_far=aggregate_so_far,
                )
                # Update the running aggregate AFTER the block is
                # finalized (post-persistence, so the wrapper message
                # size is what counts toward the budget — not the
                # original 200K output).
                tool_use_context.tool_result_chars_so_far += compute_block_chars(api_block)
            content_str = api_block.get("content", "")
            if not isinstance(content_str, str):
                content_str = json.dumps(content_str, ensure_ascii=False)
        elif isinstance(result.output, str):
            content_str = result.output
        elif isinstance(result.output, dict):
            content_str = json.dumps(result.output, ensure_ascii=False)
        else:
            content_str = str(result.output)

        return UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=block.id,
                    content=content_str,
                    is_error=result.is_error,
                    metadata=metadata,
                )
            ],
        )
    except Exception as e:
        error_str = f"Error: {e}"
        return UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=block.id,
                    content=error_str,
                    is_error=True,
                )
            ],
        )


async def _run_tools_partitioned(
    tool_use_blocks: list[ToolUseBlock],
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
    tools: Tools,
) -> list[UserMessage]:
    """**DEPRECATED** — ch07 / Phase 2e replaced this with
    ``_collect_tool_results``. The production query loop no longer
    invokes this function. Retained only for
    ``tests/parity/test_orchestrator_partitioned_parity.py`` and the
    test_tool_result_budget shim path. Remove together with
    ``_dispatch_single_tool`` and ``_partition_tool_calls`` after the
    test rewrite (Option A in the refactoring plan).

    Run tools with TS-matching concurrency: safe tools parallel, unsafe exclusive.
    Mirrors typescript/src/tools/partitionToolCalls + runTools (Mode 2).
    """
    batches = _partition_tool_calls(tool_use_blocks, tools)
    all_results: list[UserMessage] = []

    for batch in batches:
        if batch.is_concurrent_safe and len(batch.blocks) > 1:
            coros = [
                asyncio.to_thread(
                    _dispatch_single_tool, block, tool_registry, tool_use_context, tools,
                )
                for block in batch.blocks[:MAX_TOOL_USE_CONCURRENCY]
            ]
            batch_results = await asyncio.gather(*coros)
            all_results.extend(batch_results)
            if len(batch.blocks) > MAX_TOOL_USE_CONCURRENCY:
                overflow = [
                    asyncio.to_thread(
                        _dispatch_single_tool, block, tool_registry, tool_use_context, tools,
                    )
                    for block in batch.blocks[MAX_TOOL_USE_CONCURRENCY:]
                ]
                all_results.extend(await asyncio.gather(*overflow))
        else:
            for block in batch.blocks:
                result = await asyncio.to_thread(
                    _dispatch_single_tool, block, tool_registry, tool_use_context, tools,
                )
                all_results.append(result)

    return all_results


def _run_tools_sync(
    tool_use_blocks: list[ToolUseBlock],
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
) -> list[UserMessage]:
    """Legacy synchronous tool execution (no partitioning)."""
    results: list[UserMessage] = []
    for block in tool_use_blocks:
        results.append(_dispatch_single_tool(block, tool_registry, tool_use_context))
    return results


# ============================================================================
# ch07 Phase 2d/2e — orchestrator-backed dispatch
# ============================================================================
#
# `_collect_tool_results` replaces `_run_tools_partitioned` as the production
# tool-dispatch entry point. It drives `orchestrator.run_tools()` (which goes
# through `run_tool_use` and so honours PreToolUse/PostToolUse hooks), then
# flattens the orchestrator's `MessageUpdate` stream into the `UserMessage`
# list shape the query loop expects.
#
# `_build_can_use_tool_for_query` builds the `can_use_tool` callback that
# `tool_hooks.resolve_hook_permission_decision` invokes. Concurrent batches
# treat ask → deny conservatively (avoid presenting N modals); serial
# batches go through the same fast-path (their permissions are typically
# pre-cleared by the time they reach this point).
#
# `_dispatch_single_tool` and `_partition_tool_calls` are kept as thin
# wrappers so existing test imports continue to work; production no longer
# routes through them.


def _build_can_use_tool_for_query(
    tool_registry: ToolRegistry,
) -> Callable[..., dict[str, Any]]:
    """Build the orchestrator's `can_use_tool` callback against the query
    path's permission flow.

    `tool_hooks.resolve_hook_permission_decision` calls this with both
    5-arg and 6-arg shapes — the 6-arg form has `force_decision` as a
    trailing positional when a PreToolUse hook short-circuited to
    `behavior=ask` (`tool_hooks.py:373-381`). Accept the optional
    positional so we don't `TypeError` on that branch.

    Concurrent batches convert hook-ask AND rule-ask to deny — the
    invariant is that `is_concurrency_safe=True` tools are
    permission-pre-cleared; presenting N modals for a parallel batch
    would be a bad UX. If a real ask fires on a concurrent batch we
    log a `warning` so operators can quickly map the deny back to
    their permission rule.
    """
    from ..permissions import has_permissions_to_use_tool
    # NB: `handle_permission_ask` lives in `src.permissions.handler`; we
    # don't call it here (concurrent batches deny on ask), but it's the
    # symbol an operator may chase if they see the warning below.

    def _allow_or_deny(
        tool: Tool,
        tool_input: dict[str, Any],
        ctx: ToolContext,
        assistant_msg: Any,
        tool_use_id: str,
        force_decision: Any = None,
    ) -> dict[str, Any]:
        # When a PreToolUse hook returned behavior='ask', the resolver
        # forwards the hook decision to us as `force_decision`. We
        # honour it as deny in concurrent batches.
        if force_decision is not None:
            if isinstance(force_decision, dict):
                message = force_decision.get("message") or "Hook requested user approval; not supported in concurrent batch."
            else:
                message = getattr(force_decision, "message", None) or "Hook requested user approval; not supported in concurrent batch."
            return {"behavior": "deny", "message": message}

        decision = has_permissions_to_use_tool(
            tool, tool_input, ctx.permission_context, tool_use_context=ctx,
        )

        behavior = getattr(decision, "behavior", None)
        if behavior == "deny":
            return {
                "behavior": "deny",
                "message": getattr(decision, "message", None) or "permission denied",
            }
        if behavior == "ask":
            logger.warning(
                "Concurrent batch: permission decision for tool %s is 'ask' "
                "(decision_reason=%r); concurrent batches convert ask → deny "
                "to avoid presenting parallel modals. Move this tool to a "
                "rule-based allow or accept that it'll deny in concurrent batches.",
                tool.name, getattr(decision, "decision_reason", None),
            )
            return {
                "behavior": "deny",
                "message": "Permission ask is not supported in concurrent batches.",
            }
        # allow path
        return {
            "behavior": "allow",
            "updatedInput": getattr(decision, "updated_input", None) or tool_input,
        }

    return _allow_or_deny


def _normalize_tool_result_blocks(msg: UserMessage) -> UserMessage:
    """Convert dict tool_result content blocks to `ToolResultBlock`
    dataclasses, preserving the legacy `_run_tools_partitioned` shape.

    `run_tool_use` emits content as a list of `dict` blocks; the rest of
    the codebase (REPL render, tests, etc.) expects `ToolResultBlock`
    instances. Coerce in place so the divergence stays inside this
    helper rather than rippling into consumers.

    Metadata note: the legacy `_dispatch_single_tool` attached
    ``metadata={"tool_output": result.output}`` for tools returning
    structured (dict-shaped) output, which `ToolResultBlock.metadata`
    documents as the carrier for REPL/TUI rich previews (e.g. Edit's
    structuredPatch). The wire-shape dict we see here no longer carries
    the original `result.output`; we set `metadata` to the empty dict
    and leave the structured-output forwarding as a follow-up — a
    future TUI render that depends on `tool_output` for tools that go
    through the concurrent batch path will see an empty metadata dict.
    No current consumer reads it (verified by grep).
    """
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return msg
    new_content = []
    changed = False
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            content_val = b.get("content", "")
            # Pass-through for structured content (e.g. lists with
            # image blocks); ToolResultBlock supports list content
            # directly.
            new_content.append(ToolResultBlock(
                tool_use_id=b.get("tool_use_id", ""),
                content=content_val,
                is_error=bool(b.get("is_error", False)),
            ))
            changed = True
        else:
            new_content.append(b)
    if changed:
        msg.content = new_content
    return msg


async def _collect_tool_results(
    tool_use_blocks: list[ToolUseBlock],
    assistant_messages: list[AssistantMessage],
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
    tools: Tools,
) -> tuple[list[UserMessage], ToolContext]:
    """Drive orchestrator.run_tools and flatten its updates into a
    `UserMessage` list.

    Mirrors ch07 §"Batch Execution". Replaces `_run_tools_partitioned`
    as the production tool-dispatch entry point.

    Returns `(results, last_context)`. The context may have been
    modified by concurrent-batch queued modifiers; the caller threads
    it into the next turn.
    """
    from ..services.tool_execution.orchestrator import run_tools
    from ..types.messages import AttachmentMessage

    # ``orchestrator.partition_tool_calls`` and ``run_tool_use`` both
    # look up tools via ``tool_use_context.options.tools``. The query
    # loop has historically passed tools as a separate parameter and
    # not synced them onto the context. Unconditionally overwrite so
    # the context reflects this turn's tool list (idempotent for the
    # common case where the list is unchanged turn-to-turn; correct
    # if/when a future contributor adds dynamic tool refresh).
    if tools:
        tool_use_context.options.tools = list(tools)

    can_use_tool = _build_can_use_tool_for_query(tool_registry)
    results: list[UserMessage] = []
    last_context = tool_use_context

    async for update in run_tools(
        tool_use_blocks, assistant_messages, can_use_tool, tool_use_context,
    ):
        if update.new_context is not None:
            last_context = update.new_context
        msg = update.message
        if msg is None:
            continue
        if isinstance(msg, AttachmentMessage):
            # Post-hook attachments (e.g. should_prevent_continuation
            # extra context). Not surfaced today along the partition
            # path; drop with a DEBUG log. Wiring them into the
            # next-turn message stream is a follow-up.
            logger.debug(
                "Concurrent batch produced an AttachmentMessage; "
                "dropping (not surfaced in this path). attachments=%s",
                getattr(msg, "attachments", None),
            )
            continue
        if isinstance(msg, UserMessage):
            results.append(_normalize_tool_result_blocks(msg))
        else:
            # SystemMessage / progress message / etc — side-channel.
            logger.debug(
                "Dropping side-channel message of type %s",
                type(msg).__name__,
            )

    return results, last_context


async def query(params: QueryParams) -> AsyncGenerator[Message | StreamEvent, None]:
    _diag = os.environ.get("CLAWCODEX_DEBUG", "").lower() in ("1", "true", "yes")
    state = QueryState(
        messages=list(params.messages),
        tool_use_context=params.tool_use_context,
        max_output_tokens_override=params.max_output_tokens_override,
    )
    config = build_query_config()
    terminal: Terminal | None = None

    while True:
        messages = state.messages
        if _diag:
            logger.warning(
                "[DIAG] query loop: turn=%d  messages=%d  transition=%s",
                state.turn_count, len(messages),
                state.transition.reason if state.transition else "initial",
            )
        tool_use_context = state.tool_use_context
        # WI-5.1: reset the per-message aggregate counter at each turn
        # boundary. The 200K cap is PER USER MESSAGE (the next batch of
        # tool_result blocks the model will see), not per session. Without
        # this reset the counter grows monotonically and every tool result
        # eventually gets persisted regardless of size. Mirrors TS
        # ``toolResultStorage.ts:collectCandidatesByMessage`` which
        # partitions evaluation by message.
        tool_use_context.tool_result_chars_so_far = 0
        max_output_tokens_recovery_count = state.max_output_tokens_recovery_count
        has_attempted_reactive_compact = state.has_attempted_reactive_compact
        max_output_tokens_override = state.max_output_tokens_override
        turn_count = state.turn_count

        yield StreamEvent(type="stream_request_start")

        # --- Phase 0: Compression Pipeline ---
        # Mirrors TS query loop Phase 0: toolResultBudget → snip → microcompact → collapse → autocompact
        if params.pipeline_config is not None:
            try:
                # Estimate input tokens so layer 5 (autocompact) can decide
                # whether to fire. Without this the MIN_INPUT_TOKENS_FOR_AUTOCOMPACT
                # guard short-circuits and auto-compact never triggers.
                est_input_tokens = rough_token_count_estimation_for_messages(messages)
                pipeline_result = await run_compression_pipeline(
                    messages,
                    input_token_count=est_input_tokens,
                    config=params.pipeline_config,
                )
                if pipeline_result.tokens_saved > 0:
                    messages = pipeline_result.messages
                    if _diag:
                        logger.warning(
                            "[DIAG] Compression pipeline saved %d tokens (layers: %s)",
                            pipeline_result.tokens_saved,
                            ", ".join(pipeline_result.layers_applied),
                        )
            except Exception:
                logger.warning("Compression pipeline failed, continuing with original messages", exc_info=True)

        assistant_messages: list[AssistantMessage] = []
        tool_results: list[UserMessage] = []
        tool_use_blocks: list[ToolUseBlock] = []
        needs_follow_up = False

        try:
            returned_assistants, returned_tool_blocks = await _call_model_sync(
                provider=params.provider,
                messages=messages,
                system_prompt=params.system_prompt,
                tools=params.tools,
                max_output_tokens_override=max_output_tokens_override,
            )
            assistant_messages = returned_assistants
            tool_use_blocks = returned_tool_blocks
            needs_follow_up = len(tool_use_blocks) > 0

            for msg in assistant_messages:
                withheld = False
                if _is_withheld_max_output_tokens(msg):
                    withheld = True
                if not withheld:
                    yield msg

        except Exception as e:
            logger.error("Query error: %s", e)
            error_message = str(e)

            for err_msg in _yield_missing_tool_result_blocks(assistant_messages, error_message):
                yield err_msg

            yield _create_assistant_api_error_message(content=error_message)
            return

        if params.abort_controller.signal.aborted:
            for err_msg in _yield_missing_tool_result_blocks(
                assistant_messages, "Interrupted by user"
            ):
                yield err_msg

            if params.abort_controller.signal.reason != "interrupt":
                yield _create_user_interruption_message(tool_use=False)
            return

        if not needs_follow_up:
            last_message = assistant_messages[-1] if assistant_messages else None

            if _is_withheld_max_output_tokens(last_message):
                if (
                    max_output_tokens_override is None
                    and max_output_tokens_recovery_count == 0
                ):
                    state = QueryState(
                        messages=messages,
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=state.auto_compact_tracking,
                        max_output_tokens_recovery_count=max_output_tokens_recovery_count,
                        has_attempted_reactive_compact=has_attempted_reactive_compact,
                        max_output_tokens_override=ESCALATED_MAX_TOKENS,
                        stop_hook_active=None,
                        turn_count=turn_count,
                        transition=Transition(reason="max_output_tokens_escalate"),
                    )
                    continue

                if max_output_tokens_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                    recovery_message = _create_user_message(
                        "Output token limit hit. Resume directly — no apology, no recap of what you were doing. "
                        "Pick up mid-thought if that is where the cut happened. Break remaining work into smaller pieces.",
                        is_meta=True,
                    )
                    state = QueryState(
                        messages=[*messages, *assistant_messages, recovery_message],
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=state.auto_compact_tracking,
                        max_output_tokens_recovery_count=max_output_tokens_recovery_count + 1,
                        has_attempted_reactive_compact=has_attempted_reactive_compact,
                        max_output_tokens_override=None,
                        stop_hook_active=None,
                        turn_count=turn_count,
                        transition=Transition(
                            reason="max_output_tokens_recovery",
                            attempt=max_output_tokens_recovery_count + 1,
                        ),
                    )
                    continue

                yield last_message  # type: ignore[arg-type]

            if last_message and getattr(last_message, "isApiErrorMessage", False):
                return

            return

        for block in tool_use_blocks:
            yield SystemMessage(
                content=f"Running tool: {block.name}",
                subtype="tool_use_progress",
            )

        if _diag:
            _tools_t0 = time.monotonic()
            # ch07 §2e — diagnostic now uses the orchestrator's
            # `partition_tool_calls` (signature takes a context).
            from ..services.tool_execution.orchestrator import partition_tool_calls as _orch_partition
            _batches = _orch_partition(tool_use_blocks, tool_use_context)
            _batch_desc = ", ".join(
                f"[{'parallel' if b.is_concurrency_safe else 'exclusive'}: {[bl.name for bl in b.blocks]}]"
                for b in _batches
            )
            logger.warning(
                "[DIAG] query loop: running %d tools in %d batches: %s",
                len(tool_use_blocks), len(_batches), _batch_desc,
            )

        # ch07 §2e — production cutover. The query loop now drives the
        # orchestrator (which goes through run_tool_use, honouring
        # PreToolUse/PostToolUse hooks). The flattened result list
        # preserves the legacy UserMessage shape; the threaded
        # `tool_use_context` carries any concurrent-batch context
        # modifiers into the next turn.
        tool_results, tool_use_context = await _collect_tool_results(
            tool_use_blocks,
            assistant_messages,
            params.tool_registry,
            tool_use_context,
            params.tools,
        )

        if _diag:
            logger.warning(
                "[DIAG] query loop: tools finished in %.1fs, %d results",
                time.monotonic() - _tools_t0, len(tool_results),
            )
            for tr in tool_results:
                if isinstance(tr.content, list):
                    for b in tr.content:
                        if hasattr(b, 'content'):
                            clen = len(b.content) if isinstance(b.content, str) else len(str(b.content))
                            logger.warning("[DIAG]   result: tool_use_id=%s  is_error=%s  content_len=%d", getattr(b, 'tool_use_id', '?'), getattr(b, 'is_error', False), clen)

        for result_msg in tool_results:
            yield result_msg

        if params.abort_controller.signal.aborted:
            if params.abort_controller.signal.reason != "interrupt":
                yield _create_user_interruption_message(tool_use=True)
            return

        next_turn_count = turn_count + 1

        if params.max_turns and next_turn_count > params.max_turns:
            yield _create_max_turns_attachment(params.max_turns, next_turn_count)
            return

        # Chapter-10 / Chunk D / WI-3.3 — pending-messages drain at the
        # tool-round boundary. Per chapter §"Background: Three Channels":
        # *"messages arrive between tool rounds, not mid-execution. The
        # agent finishes its current thought, then receives the new
        # information."* We drain AFTER tool_results have been appended
        # but BEFORE the next API call, so the model sees the queued
        # messages on the next turn alongside the tool results.
        injected_messages = _drain_pending_user_messages(tool_use_context)
        for inj in injected_messages:
            yield inj

        state = QueryState(
            messages=[*messages, *assistant_messages, *tool_results, *injected_messages],
            tool_use_context=tool_use_context,
            auto_compact_tracking=state.auto_compact_tracking,
            turn_count=next_turn_count,
            max_output_tokens_recovery_count=0,
            has_attempted_reactive_compact=False,
            max_output_tokens_override=None,
            stop_hook_active=state.stop_hook_active,
            transition=Transition(reason="next_turn"),
        )
