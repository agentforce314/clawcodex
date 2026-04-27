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
    system_prompt: str
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
        logger.warning(
            "[DIAG] _call_model_sync: %d api_messages, ~%d chars, system_prompt=%d chars, %d tools",
            len(api_messages), _total_chars, len(system_prompt), len(list(tools)),
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
        call_kwargs["system"] = system_prompt
    else:
        api_messages = [{"role": "system", "content": system_prompt}, *api_messages]

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
    )
    if response.reasoning_content:
        # Preserve provider thinking metadata for follow-up turns.
        assistant_msg.reasoning_content = response.reasoning_content  # type: ignore[attr-defined]

    if stop_reason == "max_tokens":
        assistant_msg._api_error = "max_output_tokens"  # type: ignore[attr-defined]
        assistant_msg.isApiErrorMessage = False

    return [assistant_msg], tool_use_blocks


# Max tools to run in parallel (TS default: 10, configurable via env var)
MAX_TOOL_USE_CONCURRENCY = int(
    os.environ.get("CLAWCODEX_MAX_TOOL_USE_CONCURRENCY", "10")
)


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

    Uses tool.map_result_to_api() (mirrors TS mapToolResultToAPIMessage)
    to convert structured output (e.g. file_unchanged) to API-ready text.
    """
    try:
        call = ToolCall(
            name=block.name,
            input=block.input,
            tool_use_id=block.id,
        )
        result = tool_registry.dispatch(call, tool_use_context)

        tool = find_tool_by_name(tools, block.name) if tools else None
        if tool is not None:
            api_block = tool.map_result_to_api(result.output, block.id)
            content_str = api_block.get("content", "")
            if not isinstance(content_str, str):
                content_str = json.dumps(content_str, ensure_ascii=False)
        elif isinstance(result.output, str):
            content_str = result.output
        elif isinstance(result.output, dict):
            content_str = json.dumps(result.output, ensure_ascii=False)
        else:
            content_str = str(result.output)

        # Preserve the original tool output as in-process metadata so the
        # REPL/TUI can render rich previews (Edit's structuredPatch is the
        # current consumer). map_result_to_api strips it for the wire.
        metadata: dict[str, Any] = {}
        if isinstance(result.output, dict):
            metadata["tool_output"] = result.output
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
            return

        next_turn_count = turn_count + 1

        if params.max_turns and next_turn_count > params.max_turns:
            yield _create_max_turns_attachment(params.max_turns, next_turn_count)
            return

        state = QueryState(
            messages=[*messages, *assistant_messages, *tool_results],
            tool_use_context=tool_use_context,
            auto_compact_tracking=state.auto_compact_tracking,
            turn_count=next_turn_count,
            max_output_tokens_recovery_count=0,
            has_attempted_reactive_compact=False,
            max_output_tokens_override=None,
            stop_hook_active=state.stop_hook_active,
            transition=Transition(reason="next_turn"),
        )
