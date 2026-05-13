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


def _find_assistant_message_for_block(
    block: ToolUseBlock,
    assistant_messages: list[AssistantMessage],
) -> AssistantMessage | None:
    """Find the AssistantMessage that emitted ``block.id``.

    Tool calls always live inside the assistant turn that produced them;
    hooks may need access to that surrounding turn. Returns None if no
    match (the adapter can use a stub).
    """
    for msg in assistant_messages:
        content = msg.content
        if not isinstance(content, list):
            continue
        for content_block in content:
            if isinstance(content_block, ToolUseBlock) and content_block.id == block.id:
                return msg
    return None


def _dispatch_single_tool(
    block: ToolUseBlock,
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
    tools: Tools | None = None,
    assistant_message: AssistantMessage | None = None,
) -> tuple[list[UserMessage], Any]:
    """Dispatch a single tool through the full 13-step pipeline.

    Returns a ``(messages, context_modifier)`` tuple:

    * ``messages`` -- the primary tool_result UserMessage plus any
      auxiliary messages (sub-agent transcripts injected via
      ``ToolResult.new_messages``, hook attachments) the pipeline
      produced. Caller appends them all to the conversation in order.
    * ``context_modifier`` -- optional callable returned by tools like
      ``EnterPlanMode`` that mutates the ``ToolContext`` for
      subsequent tool calls. Caller is responsible for applying it
      (serial batches: apply immediately; concurrent batches: queue
      until batch completes).

    Routes through ``dispatch_full`` which internally runs the full
    pipeline: schema/semantic validation, ``backfill_observable_input``,
    PreToolUse hooks, permission resolution, tool execution, Step 11
    result budgeting (per-tool ``max_result_size_chars`` AND aggregate
    per-message cap), PostToolUse hooks, ``new_messages`` injection,
    and telemetry-safe error classification. The previous implementation
    bolted only Step 11 onto ``tool_registry.dispatch()`` and silently
    skipped hooks, ``new_messages``, ``context_modifier``, and error
    classification — see ``my-docs/ch06-tools-gap-analysis.md``.

    ``tool_registry`` is kept as a parameter for back-compat / future
    use; the dispatch lookup itself goes through the ``tools`` list.

    **Abort handling.** ``dispatch_full → run_tool_use`` checks
    ``tool_use_context.abort_controller.signal.aborted`` at the top
    of every tool call (``tool_execution.py:99-105``) and yields a
    ``CANCEL_MESSAGE`` tool_result if set. No additional check is
    needed here — every dispatch inherits boundary-abort behavior
    for free. Mid-tool-execution abort (e.g., interrupting a long
    Bash command) is NOT yet supported; deferred to a follow-up that
    moves Bash to ``asyncio.create_subprocess_exec`` with cancellation.
    """
    from ..services.tool_execution import (
        dispatch_full,
        make_stub_assistant_message,
    )

    try:
        call = ToolCall(
            name=block.name,
            input=block.input,
            tool_use_id=block.id,
        )

        amsg = assistant_message or make_stub_assistant_message()
        result = dispatch_full(
            call,
            tool_use_context,
            amsg,
            tools=list(tools) if tools else None,
        )

        # Extract content from the primary tool_result block produced
        # by the pipeline. The block content is already mapped +
        # budgeted by Step 11.
        content_val = result.tool_result_block.get("content", "")
        if not isinstance(content_val, str):
            content_str = json.dumps(content_val, ensure_ascii=False)
        else:
            content_str = content_val

        metadata: dict[str, Any] = {}
        if isinstance(result.output, dict):
            metadata["tool_output"] = result.output

        primary = UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=block.id,
                    content=content_str,
                    is_error=result.is_error,
                    metadata=metadata,
                )
            ],
        )

        # Surface new_messages (sub-agent transcripts, system reminders,
        # hook attachments) as additional UserMessages appended after
        # the primary result. The pipeline emits them as Message
        # objects; the conversation accepts them as-is.
        out_msgs: list[UserMessage] = [primary]
        for extra in result.new_messages:
            # The pipeline yields Message subclasses; coerce attachment
            # / system messages into the UserMessage shape only when
            # they aren't already a Message. Otherwise pass through.
            if isinstance(extra, UserMessage):
                out_msgs.append(extra)
            elif isinstance(extra, AssistantMessage):
                # Unusual but the pipeline could surface attachment-as-assistant
                # in some hook configurations; emit it via wrap.
                out_msgs.append(extra)  # type: ignore[arg-type]
            else:
                # Some hook outputs are AttachmentMessage / SystemMessage;
                # keep them as-is. They share the Message base.
                out_msgs.append(extra)  # type: ignore[arg-type]

        return out_msgs, result.context_modifier
    except Exception as e:
        error_str = f"Error: {e}"
        return ([UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=block.id,
                    content=error_str,
                    is_error=True,
                )
            ],
        )], None)


async def _run_tools_partitioned(
    tool_use_blocks: list[ToolUseBlock],
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
    tools: Tools,
    assistant_messages: list[AssistantMessage] | None = None,
) -> list[UserMessage]:
    """Run tools with TS-matching concurrency: safe tools parallel, unsafe exclusive.

    Mirrors typescript/src/tools/partitionToolCalls + runTools (Mode 2).
    ConcurrencySafe tools (Read, Grep, Glob, etc.) run in parallel up to
    MAX_TOOL_USE_CONCURRENCY.  Non-safe tools (Bash, Edit, Write) run
    exclusively one at a time.

    ``context_modifier`` application order matches TS semantics:

    * **Concurrent batches** (multiple parallel calls): queue per-tool
      modifiers, then apply in submission order AFTER the batch
      completes. This is the only safe choice because two parallel
      tools both modifying the context have undefined interleaving.
    * **Serial batches**: apply the modifier immediately after each
      tool returns, so the next tool in the same batch sees the
      mutated context. Without this, ``[EnterPlanMode, Edit(/src/x)]``
      in one turn would run Edit with the pre-EnterPlanMode permission
      mode.

    The ``_aggregate_lock`` (for the per-message 200K budget) is held
    inside ``run_tool_use`` at the narrow read-decide-write window,
    NOT at this call site. Lock-at-call-site would serialize the
    parallel I/O that concurrency-safe tools exist to enable
    (Read/Grep/Glob in parallel).

    ``context_modifier`` may legally return a new (cloned) context.
    Both branches below assign the return value back to
    ``current_context`` so a clone-style modifier propagates to
    subsequent batches; the closure passed to ``asyncio.to_thread``
    captures the latest ``current_context`` per dispatch.
    """
    asst_msgs = assistant_messages or []
    batches = _partition_tool_calls(tool_use_blocks, tools)
    all_results: list[UserMessage] = []
    current_context = tool_use_context

    def _run_one(block: ToolUseBlock, ctx: ToolContext) -> tuple[list[UserMessage], Any]:
        amsg = _find_assistant_message_for_block(block, asst_msgs)
        return _dispatch_single_tool(
            block, tool_registry, ctx, tools, amsg,
        )

    for batch in batches:
        if batch.is_concurrent_safe and len(batch.blocks) > 1:
            # Concurrent batch: queue context_modifiers; apply after.
            # Snapshot the current context for the whole batch so every
            # parallel call sees the same context (no mid-batch race).
            batch_ctx = current_context
            queued_modifiers: list[tuple[str, Any]] = []
            coros = [
                asyncio.to_thread(_run_one, block, batch_ctx)
                for block in batch.blocks[:MAX_TOOL_USE_CONCURRENCY]
            ]
            batch_results = await asyncio.gather(*coros)
            for block, (msgs, mod) in zip(
                batch.blocks[:MAX_TOOL_USE_CONCURRENCY], batch_results,
            ):
                all_results.extend(msgs)
                if mod is not None:
                    queued_modifiers.append((block.id, mod))
            if len(batch.blocks) > MAX_TOOL_USE_CONCURRENCY:
                overflow_coros = [
                    asyncio.to_thread(_run_one, block, batch_ctx)
                    for block in batch.blocks[MAX_TOOL_USE_CONCURRENCY:]
                ]
                overflow_results = await asyncio.gather(*overflow_coros)
                for block, (msgs, mod) in zip(
                    batch.blocks[MAX_TOOL_USE_CONCURRENCY:], overflow_results,
                ):
                    all_results.extend(msgs)
                    if mod is not None:
                        queued_modifiers.append((block.id, mod))

            # Apply queued context modifiers in submission order. The
            # modifier may return a clone; capture the returned context
            # so it propagates to subsequent batches.
            for _, modifier in queued_modifiers:
                try:
                    new_ctx = modifier(current_context)
                    if new_ctx is not None:
                        current_context = new_ctx
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "context_modifier failed; continuing with prior context: %s",
                        exc,
                    )
        else:
            # Serial batch: apply context_modifier immediately so the
            # next tool in the batch sees the mutated context.
            for block in batch.blocks:
                msgs, modifier = await asyncio.to_thread(_run_one, block, current_context)
                all_results.extend(msgs)
                if modifier is not None:
                    try:
                        new_ctx = modifier(current_context)
                        if new_ctx is not None:
                            current_context = new_ctx
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "context_modifier failed; continuing with prior context: %s",
                            exc,
                        )

    return all_results


def _run_tools_sync(
    tool_use_blocks: list[ToolUseBlock],
    tool_registry: ToolRegistry,
    tool_use_context: ToolContext,
) -> list[UserMessage]:
    """Legacy synchronous tool execution (no partitioning).

    Currently has no production callers; kept for back-compat with any
    SDK consumer that imports it. New callers should use
    ``_run_tools_partitioned`` (async, partition-aware).
    """
    results: list[UserMessage] = []
    current_context = tool_use_context
    for block in tool_use_blocks:
        msgs, modifier = _dispatch_single_tool(block, tool_registry, current_context)
        results.extend(msgs)
        if modifier is not None:
            try:
                new_ctx = modifier(current_context)
                if new_ctx is not None:
                    current_context = new_ctx
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "context_modifier failed; continuing with prior context: %s",
                    exc,
                )
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

            if last_message and getattr(last_message, "isApiErrorMessage", False):
                set_terminal(holder, natural_termination, Terminal(reason="completed"))
                return

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
            assistant_messages=assistant_messages,
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
