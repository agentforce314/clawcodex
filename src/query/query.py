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
    # Ch5/D.1: Task-level token budget. When set, the loop checks
    # `check_token_budget` after each completion and may continue
    # with a nudge message until 90% of `total` is reached.
    task_budget: dict[str, int] | None = None


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


def _is_withheld_prompt_too_long(msg: Message | None) -> bool:
    """Ch5/B.1: PTL withholding detector. Mirrors TS isWithheldPromptTooLong."""
    if msg is None:
        return False
    if not isinstance(msg, AssistantMessage):
        return False
    if not getattr(msg, "isApiErrorMessage", False):
        return False
    return getattr(msg, "_api_error", None) == "prompt_too_long"


def _is_withheld_media_size(msg: Message | None) -> bool:
    """Ch5/B.1: Media-size withholding detector. Mirrors TS isWithheldMediaSizeError."""
    if msg is None:
        return False
    if not isinstance(msg, AssistantMessage):
        return False
    return getattr(msg, "_api_error", None) == "media_size"


def _get_context_window(provider: Any, model: str) -> int:
    """Ch5/B.4: Best-effort context-window lookup for the blocking-limit guard.

    Defaults to 200_000 if unknown. A follow-up ticket can replace
    this with proper per-model config; for now the goal is to make
    the guard work for the common cases (Sonnet/Opus = 200k).
    """
    return getattr(provider, "context_window", None) or 200_000


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
        # Ch5/B.1: route through the typed helpers so PTL / media-size
        # detection is consistent with the rest of the codebase.
        from ..services.api.errors import (
            is_media_size_error,
            is_prompt_too_long_error,
        )
        if is_prompt_too_long_error(e):
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

        # is_media_size_error takes str (not Exception) and handles
        # case internally — pass the raw message body so the PDF-page
        # regex (which expects the literal "PDF") can match.
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
    """Canonical agent loop (chapter 5, Phase A foundation).

    The async generator yields messages and stream events to the consumer.
    The final ``Terminal`` is written to ``terminal_holder.value`` just
    before the generator returns (Python async generators cannot return
    values: PEP 525). Callers who care about the terminal pass their own
    ``TerminalHolder`` and read its ``.value`` after iteration.

    See :func:`run_query` for a convenience helper that consumes the
    generator and returns ``(messages, terminal)``.

    This PR (Phase A) introduces the typed Terminal infrastructure;
    recovery integration, stop hooks, token budget, model fallback,
    and continuation nudge land in subsequent PRs.
    """
    _diag = os.environ.get("CLAWCODEX_DEBUG", "").lower() in ("1", "true", "yes")
    holder = terminal_holder or TerminalHolder()
    # Inner-only flag for the future outer two-layer wrapper (Phase G).
    # Until that lands, the flag is local; set_terminal still writes it
    # so every exit site uses the canonical helper.
    natural_termination: list[bool] = [False]
    state = QueryState(
        messages=list(params.messages),
        tool_use_context=params.tool_use_context,
        max_output_tokens_override=params.max_output_tokens_override,
    )
    config = build_query_config()
    # Ch5/D.1: budget tracker is created only when both the runtime
    # feature flag and the per-request task_budget are set. No prompt
    # marker → no tracker → no overhead. Mirrors TS query.ts:295.
    if params.task_budget and getattr(config, "token_budget_enabled", True):
        from .token_budget import create_budget_tracker
        budget_tracker = create_budget_tracker()
    else:
        budget_tracker = None

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

                # Ch5/B.5 prereq: thread the caller-owned
                # AutoCompactTracking instance into the pipeline before
                # the call so auto_compact_if_needed mutates the same
                # object the query loop holds via state.auto_compact_tracking.
                # If the loop has no tracking yet, the pipeline creates
                # a default one — capture it back into state below.
                if state.auto_compact_tracking is not None:
                    params.pipeline_config.autocompact_tracking = (
                        state.auto_compact_tracking
                    )

                pipeline_result = await run_compression_pipeline(
                    messages,
                    input_token_count=est_input_tokens,
                    config=params.pipeline_config,
                )

                # Read the (possibly mutated) tracking object back into
                # state for the next iteration. The pipeline's
                # autocompact_tracking is now authoritative.
                state.auto_compact_tracking = (
                    params.pipeline_config.autocompact_tracking
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

        # Ch5/B.4: Blocking-limit pre-emption guard.
        # If the context is at the hard blocking limit, fail fast with
        # a clear message instead of burning an API call that will 500.
        # Mirrors TS query.ts:683-696.
        try:
            from ..services.compact.autocompact import (
                calculate_token_warning_state,
            )
            # Skip cases (mirrors TS query.ts:644-660):
            #   - compact/session_memory forked queries (deadlock risk —
            #     the compact agent must run to REDUCE token count)
            #   - the previous iteration was a recovery retry whose
            #     messages were already validated under the limit
            # We deliberately DO NOT skip when reactive_compact +
            # autocompact are both enabled. The TS guard at
            # query.ts:683-696 fires unconditionally on the proactive
            # path; reactive_compact catches the *real* 413 from the
            # API later, but this guard pre-empts the call so we don't
            # burn API budget on retries-to-500.
            skip_blocking = (
                params.query_source in ("compact", "session_memory")
                or (
                    state.transition is not None
                    and state.transition.reason
                    in ("collapse_drain_retry", "reactive_compact_retry")
                )
            )
            if not skip_blocking:
                context_window = _get_context_window(
                    params.provider,
                    getattr(config, "model", ""),
                )
                token_usage = rough_token_count_estimation_for_messages(messages)
                warning = calculate_token_warning_state(
                    token_usage,
                    context_window,
                )
                if warning.get("is_at_blocking_limit"):
                    yield _create_assistant_api_error_message(
                        PROMPT_TOO_LONG_ERROR_MESSAGE,
                        error="invalid_request",
                    )
                    set_terminal(
                        holder,
                        natural_termination,
                        Terminal(reason="blocking_limit"),
                    )
                    return
        except Exception:
            if _diag:
                logger.warning("blocking_limit pre-emption check failed", exc_info=True)

        # Ch5/B.5: autocompact-circuit-breaker pre-emption.
        # When autocompact has failed 3+ times in a row AND the context
        # is still above the autocompact threshold, fail fast — sending
        # this to the API would just 500. Mirrors TS query.ts:705-725.
        try:
            from ..services.compact.autocompact import (
                MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES,
                calculate_token_warning_state as _calc_warning,
                is_auto_compact_enabled as _is_auto_enabled,
            )
            tracking = state.auto_compact_tracking
            consec = getattr(tracking, "consecutive_failures", 0) if tracking else 0
            if (
                consec >= MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES
                and _is_auto_enabled()
            ):
                ctx_window = _get_context_window(
                    params.provider,
                    getattr(config, "model", ""),
                )
                tok_usage = rough_token_count_estimation_for_messages(messages)
                warn = _calc_warning(tok_usage, ctx_window)
                if warn.get("is_above_auto_compact_threshold"):
                    yield _create_assistant_api_error_message(
                        "The conversation has exceeded the context limit "
                        "and automatic compaction has failed. Press esc "
                        "twice to go up a few messages and try again, or "
                        "start a new session with /new.",
                        error="invalid_request",
                    )
                    set_terminal(
                        holder,
                        natural_termination,
                        Terminal(reason="blocking_limit"),
                    )
                    return
        except Exception:
            if _diag:
                logger.warning(
                    "autocompact-circuit-breaker guard failed",
                    exc_info=True,
                )

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
                # Ch5/B.1: Withhold PTL, media-size, and max-output-tokens
                # errors from the stream so SDK consumers don't disconnect
                # before the loop can attempt recovery. The withheld
                # message is still in assistant_messages for the recovery
                # dispatch below to find.
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

            # Ch5/B.2: Prompt-too-long and media-size error recovery via
            # reactive_compact. One-shot per error type
            # (has_attempted_reactive_compact gate). If recovery succeeds,
            # continue with the compacted messages. If it fails or has
            # already been attempted, surface the withheld error and exit
            # cleanly with the appropriate Terminal reason — do NOT fall
            # through to the generic isApiErrorMessage branch (which
            # would mis-report this as `completed`).
            is_withheld_ptl = _is_withheld_prompt_too_long(last_message)
            is_withheld_media = _is_withheld_media_size(last_message)

            # Ch5/B.3: Context-collapse drain runs BEFORE reactive_compact
            # for PTL only (media errors skip this — collapse can't
            # shrink images). Guarded on the previous transition: if we
            # already drained on the prior iteration, fall through to
            # reactive_compact. Mirrors TS query.ts:1160-1193.
            if is_withheld_ptl and (
                state.transition is None
                or state.transition.reason != "collapse_drain_retry"
            ):
                try:
                    from ..services.compact.context_collapse import (
                        is_context_collapse_enabled,
                        recover_from_overflow,
                    )
                    if is_context_collapse_enabled():
                        drained = recover_from_overflow(
                            messages, params.query_source,
                        )
                        if drained.committed > 0:
                            state = QueryState(
                                messages=drained.messages,
                                tool_use_context=tool_use_context,
                                auto_compact_tracking=state.auto_compact_tracking,
                                max_output_tokens_recovery_count=max_output_tokens_recovery_count,
                                has_attempted_reactive_compact=has_attempted_reactive_compact,
                                max_output_tokens_override=None,
                                stop_hook_active=None,
                                turn_count=turn_count,
                                continuation_nudge_count=state.continuation_nudge_count,
                                pending_tool_use_summary=None,
                                transition=Transition(
                                    reason="collapse_drain_retry",
                                    committed=drained.committed,
                                ),
                            )
                            continue
                except Exception:
                    # Best-effort. If staging isn't available or the
                    # drain crashes, fall through to reactive_compact.
                    # Always log at warning level (not just _diag) so a
                    # broken store is observable in production telemetry.
                    logger.warning(
                        "collapse-drain recovery failed, falling through to reactive_compact",
                        exc_info=True,
                    )

            if is_withheld_ptl or is_withheld_media:
                if not has_attempted_reactive_compact:
                    try:
                        from ..services.compact.reactive_compact import (
                            ReactiveCompactResult,
                            reactive_compact,
                        )
                        from ..services.api.errors import PromptTooLongError
                        synthetic_err = PromptTooLongError(
                            "withheld during streaming, recovering",
                        )
                        rc_result: ReactiveCompactResult = await reactive_compact(
                            messages=messages,
                            error=synthetic_err,
                            provider=params.provider,
                            model=config.model,
                        )
                    except Exception as rc_err:
                        logger.exception("reactive_compact recovery crashed: %s", rc_err)
                        rc_result = None  # type: ignore[assignment]

                    if rc_result is not None and rc_result.compacted:
                        # Yield the new messages so the consumer sees the boundary.
                        for cmsg in rc_result.messages:
                            yield cmsg
                        state = QueryState(
                            messages=rc_result.messages,
                            tool_use_context=tool_use_context,
                            auto_compact_tracking=None,
                            max_output_tokens_recovery_count=max_output_tokens_recovery_count,
                            has_attempted_reactive_compact=True,  # one-shot
                            max_output_tokens_override=None,
                            stop_hook_active=None,
                            turn_count=turn_count,
                            continuation_nudge_count=state.continuation_nudge_count,
                            pending_tool_use_summary=None,
                            transition=Transition(reason="reactive_compact_retry"),
                        )
                        continue

                # Either no recovery attempt (already ran) OR the attempt
                # failed. Surface the withheld error and exit with the
                # appropriate Terminal reason.
                if last_message is not None:
                    yield last_message
                set_terminal(
                    holder,
                    natural_termination,
                    Terminal(
                        reason="image_error" if is_withheld_media else "prompt_too_long",
                    ),
                )
                return

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
                # Death-spiral guard: when the last message is an API
                # error, do NOT run any subsequent hooks/checks (the
                # model never produced a real response). Mirrors TS
                # query.ts:1341-1344.
                set_terminal(holder, natural_termination, Terminal(reason="completed"))
                return

            # Ch5/D.2: Token budget check. If task_budget is set and we
            # haven't yet exhausted 90% of `total`, inject a nudge
            # message and continue.
            if params.task_budget and budget_tracker is not None:
                from .token_budget import ContinueDecision, check_token_budget
                global_turn_tokens = sum(
                    int((getattr(m, "usage", None) or {}).get("output_tokens", 0))
                    for m in assistant_messages
                )
                decision = check_token_budget(
                    budget_tracker,
                    getattr(tool_use_context, "agent_id", None),
                    int((params.task_budget or {}).get("total") or 0),
                    global_turn_tokens,
                )
                if isinstance(decision, ContinueDecision):
                    nudge = _create_user_message(
                        decision.nudge_message,
                        is_meta=True,
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
                        continuation_nudge_count=state.continuation_nudge_count,
                        pending_tool_use_summary=None,
                        transition=Transition(reason="token_budget_continuation"),
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
