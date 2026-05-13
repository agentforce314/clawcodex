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
from .deps import QueryDeps
from .transitions import (
    QueryState,
    Terminal,
    TerminalHolder,
    Transition,
    set_terminal,
)
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
    # Ch5/D.1 — token budget for the whole agentic turn. The shape
    # ``{"total": int}`` mirrors TS ``output_config.task_budget``. When
    # set, the no-tool-use exit path calls ``check_token_budget``; on
    # a ContinueDecision, a nudge message is injected and the loop
    # re-enters with ``transition.reason="token_budget_continuation"``.
    task_budget: dict[str, int] | None = None
    # Ch5/E.2 — provider to swap to on FallbackTriggeredError. The loop
    # uses the provider as-is until the exception fires; per critic
    # review the actual *triggering* of FallbackTriggeredError is a
    # provider-internal concern (rate-limit header parsing) that is
    # tracked as a separate ticket.
    fallback_provider: BaseProvider | None = None
    # Ch5/G.1+G.2 — narrow dependency injection. Tests pass a custom
    # ``QueryDeps`` to swap ``call_model`` for a fake without having
    # to monkey-patch ``_call_model_sync`` at the module level. When
    # ``deps`` is None, the loop falls back to ``production_deps()``.
    deps: QueryDeps | None = None


@dataclass
class StreamEvent:
    type: str
    data: Any = None


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


def _extract_assistant_text(msg: AssistantMessage) -> str:
    """Ch5/E.4 — concatenate text from an assistant message's content.

    The content may be a plain string or a list of blocks. This helper
    joins all TextBlock text into a single string for regex matching
    against continuation signals.
    """
    content = msg.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return " ".join(parts)


def _get_context_window(provider: BaseProvider) -> int:
    """Ch5/B.4 — return the model's context-window size in tokens.

    Defaults to 200_000 (the Claude family default) when the provider
    doesn't expose a real integer ``context_window`` attribute.

    The ``isinstance(_, int)`` guard handles MagicMock test fixtures:
    a MagicMock returns another mock for unset attributes, so a naive
    ``getattr(..., None) or default`` would short-circuit past the
    default. Be explicit about the type so the loop is robust to test
    doubles.
    """
    cw = getattr(provider, "context_window", None)
    if isinstance(cw, int) and cw > 0:
        return cw
    return 200_000


def _is_withheld_max_output_tokens(msg: Message | None) -> bool:
    if msg is None:
        return False
    if not isinstance(msg, AssistantMessage):
        return False
    return getattr(msg, "_api_error", None) == "max_output_tokens"


def _is_withheld_prompt_too_long(msg: Message | None) -> bool:
    """Ch5/B.1 — mirrors TS ``isWithheldPromptTooLong`` at query.ts:877.

    Returns True for an assistant message that carries an
    ``_api_error == "prompt_too_long"`` tag. The query loop checks this
    after streaming to suppress the message from the yield stream until
    PTL recovery (reactive_compact) has been attempted (B.2).
    """
    if msg is None:
        return False
    if not isinstance(msg, AssistantMessage):
        return False
    return getattr(msg, "_api_error", None) == "prompt_too_long"


def _is_withheld_media_size(msg: Message | None) -> bool:
    """Ch5/B.1 — mirrors TS ``isWithheldMediaSizeError`` at query.ts:892.

    Returns True for an assistant message that carries an
    ``_api_error == "media_size"`` tag.
    """
    if msg is None:
        return False
    if not isinstance(msg, AssistantMessage):
        return False
    return getattr(msg, "_api_error", None) == "media_size"


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

        # Ch5/B.1 — tag media-size errors so the loop can withhold them
        # and (in B.2) route through reactive-compact recovery. Mirrors
        # TS `isWithheldMediaSizeError` at query.ts:892. `is_media_size_error`
        # expects a str (substring match), so pass error_str explicitly.
        from ..services.api.errors import is_media_size_error
        if is_media_size_error(error_str):
            err_msg = _create_assistant_api_error_message(
                f"Media too large: {error_str}",
                error="media_size",
            )
            err_msg._api_error = "media_size"  # type: ignore[attr-defined]
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
    """Partition tool calls into batches per TS partitionToolCalls().

    Consecutive ConcurrencySafe tools are grouped for parallel execution.
    Non-safe tools each get their own exclusive batch.

    Mirrors TS: evaluates isConcurrencySafe per-call with actual tool input
    (not a static lookup), so e.g. read-only Bash commands can be parallel.
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
    """Dispatch a single tool and return the UserMessage result.

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
    """Run tools with TS-matching concurrency: safe tools parallel, unsafe exclusive.

    Mirrors typescript/src/tools/partitionToolCalls + runTools (Mode 2).
    ConcurrencySafe tools (Read, Grep, Glob, etc.) run in parallel up to
    MAX_TOOL_USE_CONCURRENCY.  Non-safe tools (Bash, Edit, Write) run
    exclusively one at a time.
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


async def query(
    params: QueryParams,
    *,
    terminal_holder: TerminalHolder | None = None,
) -> AsyncGenerator[Message | StreamEvent, None]:
    """Canonical agent loop — outer two-layer entry point (Ch5/G.3).

    Mirrors TS ``query.ts:224-243``. The outer wrapper delegates to
    :func:`_query_loop_inner` while tracking the UUIDs of slash
    commands and task notifications consumed during the turn. AFTER
    the inner loop completes naturally (not via ``.aclose()`` or
    exception), the outer fires
    ``notify_command_lifecycle(uuid, "completed")`` for every consumed
    UUID. If the inner crashes or is closed mid-iteration, the
    completion notifications are skipped — a failed turn does not
    declare its commands successful.

    The final ``Terminal`` is written to ``terminal_holder.value`` just
    before the generator returns (Python async generators cannot
    return values: PEP 525). Callers who care about the terminal pass
    their own ``TerminalHolder`` and read its ``.value`` after
    iteration. See :func:`run_query` for a convenience helper that
    returns ``(messages, terminal)``.
    """
    holder = terminal_holder or TerminalHolder()
    consumed_command_uuids: list[str] = []
    natural_termination: list[bool] = [False]

    inner = _query_loop_inner(
        params,
        terminal_holder=holder,
        consumed_command_uuids=consumed_command_uuids,
        natural_termination=natural_termination,
    )
    try:
        async for msg in inner:
            yield msg
    finally:
        # Fire completed-lifecycle ONLY on natural termination. If we
        # got here via .aclose() or an unhandled exception bubbling
        # up, ``natural_termination[0]`` is still False and we
        # silently drop the completion notifications. Mirrors TS at
        # query.ts:240-243 ("a failed turn should not mark commands
        # as successfully processed").
        if natural_termination[0] and consumed_command_uuids:
            from .command_lifecycle import notify_command_lifecycle
            for uuid in consumed_command_uuids:
                notify_command_lifecycle(uuid, "completed")


async def _query_loop_inner(
    params: QueryParams,
    *,
    terminal_holder: TerminalHolder,
    consumed_command_uuids: list[str],
    natural_termination: list[bool],
) -> AsyncGenerator[Message | StreamEvent, None]:
    """Inner agent loop — does all the real work (chapter 5).

    Separated from the outer ``query()`` wrapper so the outer can
    gate command-lifecycle dispatch on natural termination. The
    ``consumed_command_uuids`` list is shared-mutable — when the
    inner consumes a slash command, it appends the UUID; the outer
    reads the final list after iteration.
    """
    _diag = os.environ.get("CLAWCODEX_DEBUG", "").lower() in ("1", "true", "yes")
    holder = terminal_holder
    state = QueryState(
        messages=list(params.messages),
        tool_use_context=params.tool_use_context,
        max_output_tokens_override=params.max_output_tokens_override,
    )
    config = build_query_config()

    # Ch5/G.1+G.2 — resolve deps once per query() invocation. Tests
    # pass their own QueryDeps to swap call_model for a fake; the
    # production path uses production_deps() which wires
    # _call_model_sync, microcompact_messages, and auto_compact_if_needed.
    if params.deps is not None:
        deps = params.deps
    else:
        from .deps import production_deps
        deps = production_deps()

    # Ch5/D.1 — instantiate the budget tracker once per query() call
    # when both the config gate (token_budget_enabled) is on AND the
    # caller passed a task_budget. Mirrors TS query.ts:295. The tracker
    # is mutated in place by check_token_budget across iterations.
    budget_tracker = None
    if config.token_budget_enabled and params.task_budget:
        from .token_budget import create_budget_tracker
        budget_tracker = create_budget_tracker()

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
        snip_tokens_freed = 0
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
                    snip_tokens_freed = pipeline_result.tokens_saved
                    if _diag:
                        logger.warning(
                            "[DIAG] Compression pipeline saved %d tokens (layers: %s)",
                            pipeline_result.tokens_saved,
                            ", ".join(pipeline_result.layers_applied),
                        )
            except Exception:
                logger.warning("Compression pipeline failed, continuing with original messages", exc_info=True)

        # Ch5/B.4 + B.5 — pre-emption guards before the API call.
        # Two distinct guards:
        #   B.4: hard blocking limit (auto-compact off OR no other
        #        recovery available) — saves the 500 the API would
        #        return anyway.
        #   B.5: autocompact circuit-breaker tripped (3 consecutive
        #        failures) AND we're still over the autocompact
        #        threshold — gives the user an actionable message
        #        instead of an opaque 500.
        # Skip when this iteration is a recovery retry (the messages
        # were already validated under the limit), or when this is a
        # compact/session_memory forked query (those need to run to
        # REDUCE the token count, blocking would deadlock).
        from ..services.compact.autocompact import (
            MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
            calculate_token_warning_state,
            is_auto_compact_enabled,
        )

        skip_blocking_guards = (
            params.query_source in ("compact", "session_memory")
            or (
                state.transition is not None
                and state.transition.reason
                in ("collapse_drain_retry", "reactive_compact_retry")
            )
        )

        if not skip_blocking_guards:
            context_window = _get_context_window(params.provider)
            # NB: ``messages`` here is already the post-pipeline list (we
            # reassigned ``messages = pipeline_result.messages`` above when
            # the pipeline freed tokens). So ``rough_token_count_estimation``
            # already reflects all compression-layer savings; subtracting
            # ``snip_tokens_freed`` again would double-count. The variable
            # is retained for B.5 logging / future TS-style snip-only
            # measurement, but the guard math uses the post-pipeline count
            # directly.
            token_usage = rough_token_count_estimation_for_messages(messages)
            warning = calculate_token_warning_state(token_usage, context_window)

            # B.5 (checked first — gives the more actionable message
            # when the breaker has tripped). Mirrors TS query.ts:705-725.
            tracking = state.auto_compact_tracking or (
                params.pipeline_config.autocompact_tracking
                if params.pipeline_config is not None
                else None
            )
            consec = (
                getattr(tracking, "consecutive_failures", 0)
                if tracking is not None
                else 0
            )
            if (
                consec >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
                and is_auto_compact_enabled()
                and warning["is_above_auto_compact_threshold"]
            ):
                yield _create_assistant_api_error_message(
                    content=(
                        "The conversation has exceeded the context limit "
                        "and automatic compaction has failed. Press esc twice "
                        "to go up a few messages and try again, or start a "
                        "new session with /new."
                    ),
                    error="invalid_request",
                )
                set_terminal(
                    holder, natural_termination, Terminal(reason="blocking_limit"),
                )
                return

            # B.4 (hard blocking limit). Mirrors TS query.ts:683-696.
            # Only fires when reactive-compact recovery is NOT available
            # — otherwise we let the API 413 and the B.2 recovery path
            # handle it. This matches TS: the guard exists to short-
            # circuit only when no recovery would catch the 500 anyway.
            elif (
                warning["is_at_blocking_limit"]
                and not (
                    config.reactive_compact_enabled
                    and is_auto_compact_enabled()
                )
            ):
                yield _create_assistant_api_error_message(
                    content=PROMPT_TOO_LONG_ERROR_MESSAGE,
                    error="invalid_request",
                )
                set_terminal(
                    holder, natural_termination, Terminal(reason="blocking_limit"),
                )
                return

        assistant_messages: list[AssistantMessage] = []
        tool_results: list[UserMessage] = []
        tool_use_blocks: list[ToolUseBlock] = []
        needs_follow_up = False

        # Ch5/E.2 — attempt-with-fallback loop. On FallbackTriggeredError,
        # swap to params.fallback_provider and retry. Mirrors TS at
        # query.ts:727-1029. Per critic-revised plan, we swap the
        # PROVIDER (not a model name kwarg), because the provider
        # object IS the model in the existing dispatch.
        current_provider = params.provider
        attempt_with_fallback = True
        from ..services.api.errors import FallbackTriggeredError
        from ..types.messages import TombstoneMessage, create_system_message

        try:
            while attempt_with_fallback:
                attempt_with_fallback = False
                try:
                    # Ch5/G.2 — route through deps.call_model. The default
                    # production deps wires this to _call_model_sync; tests
                    # can pass a fake with the same signature.
                    returned_assistants, returned_tool_blocks = await deps.call_model(
                        provider=current_provider,
                        messages=messages,
                        system_prompt=params.system_prompt,
                        tools=params.tools,
                        max_output_tokens_override=max_output_tokens_override,
                    )
                    assistant_messages = returned_assistants
                    tool_use_blocks = returned_tool_blocks
                    needs_follow_up = len(tool_use_blocks) > 0
                except FallbackTriggeredError as fb:
                    if params.fallback_provider is None:
                        # No fallback configured — re-raise into outer
                        # exception handler below (Terminal(model_error)).
                        raise
                    # Tombstone any partial assistant messages from the
                    # failed attempt so the UI removes them from the
                    # transcript. Yield orphaned-tool-result blocks so
                    # the API protocol invariant (every tool_use has a
                    # tool_result) holds for the partial state we're
                    # discarding. Mirrors TS at query.ts:978-1005.
                    for orphan_msg in _yield_missing_tool_result_blocks(
                        assistant_messages, "Model fallback triggered",
                    ):
                        yield orphan_msg
                    for partial in assistant_messages:
                        yield TombstoneMessage(message=partial)
                    assistant_messages = []
                    tool_use_blocks = []
                    # Swap provider and retry.
                    current_provider = params.fallback_provider
                    yield create_system_message(
                        f"Switched to {fb.fallback_model} due to high "
                        f"demand for {fb.original_model}",
                        level="warning",
                    )
                    attempt_with_fallback = True

            for msg in assistant_messages:
                # Ch5/B.1 — three-source withholding pattern.
                # Mirrors TS query.ts:866-903. Recoverable errors are
                # suppressed from the yield stream so SDK consumers
                # (Cowork, the desktop app) don't disconnect mid-recovery.
                # If recovery exhausts, the message is surfaced later by
                # the dispatch code in the no-follow-up branch (B.2).
                withheld = (
                    _is_withheld_max_output_tokens(msg)
                    or _is_withheld_prompt_too_long(msg)
                    or _is_withheld_media_size(msg)
                )
                if not withheld:
                    yield msg

        except Exception as e:
            logger.error("Query error: %s", e)
            error_message = str(e)

            for err_msg in _yield_missing_tool_result_blocks(assistant_messages, error_message):
                yield err_msg

            yield _create_assistant_api_error_message(content=error_message)
            set_terminal(holder, natural_termination, Terminal(reason="model_error", error=e))
            return

        if params.abort_controller.signal.aborted:
            for err_msg in _yield_missing_tool_result_blocks(
                assistant_messages, "Interrupted by user"
            ):
                yield err_msg

            if params.abort_controller.signal.reason != "interrupt":
                yield _create_user_interruption_message(tool_use=False)
            set_terminal(holder, natural_termination, Terminal(reason="aborted_streaming"))
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

            # Ch5/B.2 — PTL / media-size recovery via reactive_compact.
            # Mirrors TS query.ts:1195-1260. When the streaming model
            # returned a withheld PTL or media-size error AND we have
            # not yet attempted reactive_compact in this loop iteration,
            # try to compact and retry. The one-shot guard
            # (``has_attempted_reactive_compact``) prevents the
            # death-spiral failure mode documented in chapter §"Death
            # Spiral Guard": without it, a compact-then-still-413 loop
            # burns thousands of API calls.
            is_withheld_ptl = _is_withheld_prompt_too_long(last_message)
            is_withheld_media = _is_withheld_media_size(last_message)

            # Phase B post-critic: if the guard ALREADY tripped (we tried
            # reactive_compact this turn and the post-compact retry STILL
            # raised PTL/media), surface the withheld error and emit the
            # appropriate Terminal — do NOT fall through to the
            # "API error → Terminal(completed)" path. Mirrors TS at
            # query.ts:1244-1252.
            if (
                (is_withheld_ptl or is_withheld_media)
                and has_attempted_reactive_compact
            ):
                if last_message is not None:
                    yield last_message
                set_terminal(
                    holder,
                    natural_termination,
                    Terminal(
                        reason="image_error"
                        if is_withheld_media
                        else "prompt_too_long"
                    ),
                )
                return

            if (
                (is_withheld_ptl or is_withheld_media)
                and not has_attempted_reactive_compact
                and config.reactive_compact_enabled
            ):
                # Ch5/B.3 — context-collapse drain runs FIRST. Mirrors
                # TS query.ts:1160-1193. If the agent had been holding
                # back staged collapses, an overflow forces them now.
                # The drain is one-shot per recovery attempt: if the
                # previous transition already drained and we're STILL
                # 413'ing, fall through to reactive_compact.
                if (
                    is_withheld_ptl
                    and config.context_collapse_enabled
                    and (
                        state.transition is None
                        or state.transition.reason != "collapse_drain_retry"
                    )
                ):
                    from ..services.compact.context_collapse import (
                        is_context_collapse_enabled,
                        recover_from_overflow,
                    )
                    if is_context_collapse_enabled():
                        drained = recover_from_overflow(
                            messages, params.query_source,
                        )
                        if drained.committed > 0:
                            for msg in drained.messages:
                                yield msg
                            state = QueryState(
                                messages=drained.messages,
                                tool_use_context=tool_use_context,
                                auto_compact_tracking=state.auto_compact_tracking,
                                max_output_tokens_recovery_count=max_output_tokens_recovery_count,
                                # Don't set has_attempted yet — drain is
                                # a separate recovery layer; reactive
                                # compact still gets its one shot if
                                # the drain doesn't free enough tokens.
                                has_attempted_reactive_compact=has_attempted_reactive_compact,
                                max_output_tokens_override=None,
                                stop_hook_active=state.stop_hook_active,
                                turn_count=turn_count,
                                pending_tool_use_summary=state.pending_tool_use_summary,
                                continuation_nudge_count=state.continuation_nudge_count,
                                transition=Transition(
                                    reason="collapse_drain_retry",
                                    committed=drained.committed,
                                ),
                            )
                            continue

                from ..services.compact.reactive_compact import (
                    ReactiveCompactResult,
                    reactive_compact,
                )
                from ..services.api.errors import PromptTooLongError

                # Synthesize an exception for reactive_compact's
                # is_prompt_too_long_error check. The withheld
                # message holds the original error string; we don't
                # need to round-trip it precisely because
                # reactive_compact only uses the exception for
                # classification.
                synthetic_err = PromptTooLongError(
                    "withheld during streaming, recovering"
                )
                result: ReactiveCompactResult = await reactive_compact(
                    messages=messages,
                    error=synthetic_err,
                    provider=params.provider,
                    model=config.model,
                )
                if result.compacted:
                    # ReactiveCompactResult.messages is list[Message]
                    # (verified 2026-05-12 against reactive_compact.py
                    # :33-39, :205-210, :230-236; the field
                    # concatenates CompactionResult.summary_messages
                    # which is list[UserMessage], with
                    # messages_to_keep which is list[Message]).
                    post_compact_messages: list[Message] = result.messages
                    for msg in post_compact_messages:
                        yield msg
                    # Critic finding (Phase B post-review): a successful
                    # reactive_compact MUST reset the engine's autocompact
                    # circuit-breaker counter. Otherwise the next iteration's
                    # B.5 guard re-reads the engine's persistent
                    # ``consecutive_failures`` (still ≥3 if the breaker
                    # tripped earlier in the session) and would trip
                    # ``Terminal(blocking_limit)`` immediately even though
                    # we just successfully compacted. Mirrors TS query.ts
                    # auto-compact success path (resets failures to 0).
                    if (
                        params.pipeline_config is not None
                        and params.pipeline_config.autocompact_tracking is not None
                    ):
                        params.pipeline_config.autocompact_tracking.consecutive_failures = 0
                    state = QueryState(
                        messages=post_compact_messages,
                        tool_use_context=tool_use_context,
                        # Carry the engine's tracking through the retry so
                        # next iteration's B.5 reads the post-reset count.
                        auto_compact_tracking=(
                            params.pipeline_config.autocompact_tracking
                            if params.pipeline_config is not None
                            else None
                        ),
                        max_output_tokens_recovery_count=max_output_tokens_recovery_count,
                        has_attempted_reactive_compact=True,  # one-shot
                        max_output_tokens_override=None,
                        stop_hook_active=state.stop_hook_active,
                        turn_count=turn_count,
                        pending_tool_use_summary=state.pending_tool_use_summary,
                        continuation_nudge_count=state.continuation_nudge_count,
                        transition=Transition(reason="reactive_compact_retry"),
                    )
                    continue

                # No recovery — surface the (until-now withheld) error
                # and exit with the appropriate Terminal reason.
                # DEATH-SPIRAL GUARD: do NOT fall through to a future
                # stop-hooks call here. Mirrors TS query.ts:1244-1252
                # ("error -> hook blocking -> retry -> error -> ...
                # the hook injects more tokens each cycle"). When C.1
                # lands the stop-hooks dispatch, this early return
                # must remain.
                if last_message is not None:
                    yield last_message
                set_terminal(
                    holder,
                    natural_termination,
                    Terminal(
                        reason="image_error"
                        if is_withheld_media
                        else "prompt_too_long"
                    ),
                )
                return

            # Ch5/C.3 — death-spiral guard. Skip stop hooks when the last
            # message is an API error. Mirrors TS query.ts:1340-1345
            # ("error → hook blocking → retry → error → ... the hook
            # injects more tokens each cycle"). Without this guard, a
            # blocking Stop hook on an API-error turn would force a
            # retry that just produces another API error in a loop.
            if last_message and getattr(last_message, "isApiErrorMessage", False):
                set_terminal(holder, natural_termination, Terminal(reason="completed"))
                return

            # Ch5/C.1 — stop-hooks dispatch at no-tool-use exit.
            # Mirrors TS query.ts:1346-1386. Stop hooks evaluate whether
            # the model is actually done. If a hook returns
            # `prevent_continuation`, exit with `stop_hook_prevented`.
            # If a hook returns blocking errors, inject them and loop
            # once more with `stop_hook_active=True` (Ch5/C.2) and
            # `has_attempted_reactive_compact` preserved (Ch5/C.4).
            if config.stop_hooks_enabled:
                from .stop_hooks import (
                    handle_stop_hooks_streaming,
                    StopHookResult,
                )

                stop_result: StopHookResult | None = None
                async for emitted in handle_stop_hooks_streaming(
                    messages_for_query=messages,
                    assistant_messages=assistant_messages,
                    system_prompt=params.system_prompt,
                    tool_use_context=tool_use_context,
                    query_source=params.query_source,
                    stop_hook_active=state.stop_hook_active,
                ):
                    if isinstance(emitted, StopHookResult):
                        stop_result = emitted
                    else:
                        yield emitted

                if stop_result is not None:
                    if stop_result.prevent_continuation:
                        set_terminal(
                            holder,
                            natural_termination,
                            Terminal(reason="stop_hook_prevented"),
                        )
                        return

                    if stop_result.blocking_errors:
                        # Ch5/C.2 + C.4 — blocking-retry path. Inject the
                        # hook errors as user messages, set
                        # `stop_hook_active=True` to suppress re-firing
                        # the same hooks on the next iteration, AND
                        # preserve `has_attempted_reactive_compact` so a
                        # prior PTL recovery is not retried in the loop
                        # (chapter §"Death Spiral Guard" point 5 —
                        # "Resetting to false here caused an infinite
                        # loop burning thousands of API calls").
                        state = QueryState(
                            messages=[
                                *messages,
                                *assistant_messages,
                                *stop_result.blocking_errors,
                            ],
                            tool_use_context=tool_use_context,
                            auto_compact_tracking=state.auto_compact_tracking,
                            max_output_tokens_recovery_count=0,
                            has_attempted_reactive_compact=(
                                has_attempted_reactive_compact
                            ),
                            max_output_tokens_override=None,
                            stop_hook_active=True,  # Ch5/C.2 suppress refire
                            turn_count=turn_count,
                            pending_tool_use_summary=state.pending_tool_use_summary,
                            continuation_nudge_count=state.continuation_nudge_count,
                            transition=Transition(reason="stop_hook_blocking"),
                        )
                        continue

            # Ch5/D.2 — token budget continuation check. Runs AFTER
            # stop hooks pass and BEFORE the continuation-nudge (E.4).
            # When the budget says "continue", inject a meta nudge with
            # remaining-budget info and re-enter the loop. Mirrors TS
            # query.ts:1388-1436.
            if budget_tracker is not None and params.task_budget is not None:
                from .token_budget import (
                    ContinueDecision,
                    check_token_budget,
                )
                global_turn_tokens = sum(
                    (m.usage or {}).get("output_tokens", 0)
                    for m in assistant_messages
                )
                decision = check_token_budget(
                    budget_tracker,
                    getattr(tool_use_context, "agent_id", None),
                    params.task_budget.get("total"),
                    global_turn_tokens,
                )
                if isinstance(decision, ContinueDecision):
                    nudge = _create_user_message(
                        decision.nudge_message, is_meta=True,
                    )
                    state = QueryState(
                        messages=[*messages, *assistant_messages, nudge],
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=state.auto_compact_tracking,
                        max_output_tokens_recovery_count=0,
                        has_attempted_reactive_compact=False,
                        max_output_tokens_override=None,
                        stop_hook_active=None,
                        turn_count=turn_count,
                        pending_tool_use_summary=state.pending_tool_use_summary,
                        continuation_nudge_count=state.continuation_nudge_count,
                        transition=Transition(reason="token_budget_continuation"),
                    )
                    continue
                # else: StopDecision — fall through to completion. The
                # decision.completion_event is logged by callers when
                # diminishing-returns early-stop fires.

            # Ch5/E.4 — continuation nudge. After the token-budget check
            # declines to continue (or no budget was set), inspect the
            # last assistant text. If it signals intent to continue
            # ("Let me now create the file"), inject a nudge user
            # message and re-enter the loop. Capped at
            # MAX_CONTINUATION_NUDGES=3 to prevent infinite nudge loops.
            # Mirrors TS query.ts:1444-1505.
            from .continuation_signals import (
                MAX_CONTINUATION_NUDGES,
                NUDGE_MESSAGE,
                matches_continuation_signal,
            )

            if (
                assistant_messages
                and (params.max_turns is None or turn_count < params.max_turns)
                and state.continuation_nudge_count < MAX_CONTINUATION_NUDGES
            ):
                last_text = _extract_assistant_text(
                    assistant_messages[-1]
                )
                if matches_continuation_signal(last_text):
                    nudge_msg = _create_user_message(
                        NUDGE_MESSAGE, is_meta=True,
                    )
                    state = QueryState(
                        messages=[*messages, *assistant_messages, nudge_msg],
                        tool_use_context=tool_use_context,
                        auto_compact_tracking=state.auto_compact_tracking,
                        max_output_tokens_recovery_count=0,
                        has_attempted_reactive_compact=False,
                        max_output_tokens_override=None,
                        stop_hook_active=None,
                        turn_count=turn_count,
                        pending_tool_use_summary=state.pending_tool_use_summary,
                        continuation_nudge_count=(
                            state.continuation_nudge_count + 1
                        ),
                        transition=Transition(reason="continuation_nudge"),
                    )
                    continue

            set_terminal(holder, natural_termination, Terminal(reason="completed"))
            return

        for block in tool_use_blocks:
            yield SystemMessage(
                content=f"Running tool: {block.name}",
                subtype="tool_use_progress",
            )

        if _diag:
            _tools_t0 = time.monotonic()
            _batches = _partition_tool_calls(tool_use_blocks, params.tools)
            _batch_desc = ", ".join(
                f"[{'parallel' if b.is_concurrent_safe else 'exclusive'}: {[bl.name for bl in b.blocks]}]"
                for b in _batches
            )
            logger.warning(
                "[DIAG] query loop: running %d tools in %d batches: %s",
                len(tool_use_blocks), len(_batches), _batch_desc,
            )

        tool_results = await _run_tools_partitioned(
            tool_use_blocks,
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
            set_terminal(holder, natural_termination, Terminal(reason="aborted_tools"))
            return

        next_turn_count = turn_count + 1

        if params.max_turns and next_turn_count > params.max_turns:
            yield _create_max_turns_attachment(params.max_turns, next_turn_count)
            set_terminal(
                holder,
                natural_termination,
                Terminal(reason="max_turns", turn_count=next_turn_count),
            )
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
            # Phase A: reset per-turn counter; carry pending summary forward.
            continuation_nudge_count=0,
            pending_tool_use_summary=state.pending_tool_use_summary,
            transition=Transition(reason="next_turn"),
        )


async def run_query(
    params: QueryParams,
) -> tuple[list[Message | StreamEvent], Terminal]:
    """Ch5/A.4: Convenience helper for callers that want both the
    yielded messages and the Terminal in one call.

    Drives the canonical :func:`query` async generator, collects all
    yielded messages into a list, and returns ``(messages, terminal)``.
    The terminal's reason discriminates why the loop stopped (10
    distinct reasons per chapter §"Terminal States").

    Tests and convenience entry points should use this helper.
    Streaming consumers (REPL, TUI) should keep using ``async for``
    with their own ``TerminalHolder``.
    """
    holder = TerminalHolder()
    messages: list[Message | StreamEvent] = []
    async for msg in query(params, terminal_holder=holder):
        messages.append(msg)
    if holder.value is None:
        # Contract violation — the loop returned without setting
        # the terminal. Fall back to a model_error so callers don't
        # see ``None`` and crash.
        holder.value = Terminal(
            reason="model_error",
            error=RuntimeError("query() returned without setting Terminal"),
        )
    return messages, holder.value
