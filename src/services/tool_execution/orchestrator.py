"""Tool orchestration — mirrors TypeScript toolOrchestration.ts.

Two modes:
- Mode 1: Streaming — use StreamingToolExecutor (called from query loop during streaming)
- Mode 2: Batch — run_tools() with partition_tool_calls() for batching
  consecutive concurrency-safe tools
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator

from src.services.tool_execution.streaming_executor import (
    MessageUpdate,
    ToolUseBlock,
    _mark_tool_use_as_complete,
)
from src.tool_system.build_tool import find_tool_by_name

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.tool_system.build_tool import Tools
    from src.tool_system.context import ToolContext
    from src.types.messages import AssistantMessage


def _get_max_tool_use_concurrency() -> int:
    # Reads CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY (chapter-7 name, used in
    # the TS reference) with the legacy CLAWCODEX_ alias + deprecation
    # warning (moved from query.py at ch07 unification).
    try:
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
    except (ValueError, TypeError):
        return 10
    return 10


@dataclass
class Batch:
    is_concurrency_safe: bool
    blocks: list[ToolUseBlock]


def classify_concurrency_safe(tool: Any, raw_input: Any) -> bool:
    """Return True iff this tool invocation is safe to parallelize.

    Mirrors the TS fail-closed pipeline (toolOrchestration.ts:96):

    1. Tool must be resolvable (unknown tools default to serial).
    2. Input must be the dict shape every ``input_schema`` expects;
       anything else (None, list, scalar) → serial.
    3. ``is_concurrency_safe`` is called inside try/except so a
       parsing bug (e.g. shell-quote crashing on a malformed Bash
       command) defaults to serial.

    The TS version also runs ``inputSchema.safeParse`` before the
    classifier; we don't have a hard jsonschema dependency in Python,
    so the dict-shape check is the closest fail-closed barrier we can
    enforce structurally. If a future version adds a strict validator,
    plug it in here — call sites will pick up the stronger guarantee
    automatically.
    """
    if tool is None:
        return False
    if not isinstance(raw_input, dict):
        return False
    try:
        return bool(tool.is_concurrency_safe(raw_input))
    except Exception:
        return False


def partition_tool_calls(
    tool_use_messages: list[ToolUseBlock],
    tool_use_context: ToolContext,
) -> list[Batch]:
    batches: list[Batch] = []
    for tool_use in tool_use_messages:
        tool = find_tool_by_name(tool_use_context.options.tools, tool_use.name)
        is_concurrency_safe = classify_concurrency_safe(tool, tool_use.input)

        if is_concurrency_safe and batches and batches[-1].is_concurrency_safe:
            batches[-1].blocks.append(tool_use)
        else:
            batches.append(Batch(is_concurrency_safe=is_concurrency_safe, blocks=[tool_use]))
    return batches


async def run_tools(
    tool_use_messages: list[ToolUseBlock],
    assistant_messages: list[AssistantMessage],
    can_use_tool: Any,
    tool_use_context: ToolContext,
) -> AsyncGenerator[MessageUpdate, None]:
    current_context = tool_use_context

    for batch in partition_tool_calls(tool_use_messages, current_context):
        if batch.is_concurrency_safe:
            # Collect context modifiers per tool, apply in tool-submission
            # order after the batch finishes. Mirrors TS
            # `queuedContextModifiers` (toolOrchestration.ts:31).
            queued_context_modifiers: dict[str, list[Any]] = {}
            async for update in _run_tools_concurrently(
                batch.blocks,
                assistant_messages,
                can_use_tool,
                current_context,
            ):
                normalized = _normalize_update(update)
                msg = normalized["message"]
                ctx_mod = normalized["context_modifier"]

                if ctx_mod is not None:
                    tool_use_id, modify_fn = _extract_modifier(ctx_mod)
                    if modify_fn is not None:
                        queued_context_modifiers.setdefault(
                            tool_use_id, [],
                        ).append(modify_fn)

                yield MessageUpdate(message=msg, new_context=current_context)

            for block in batch.blocks:
                modifiers = queued_context_modifiers.get(block.id)
                if not modifiers:
                    continue
                for modifier in modifiers:
                    current_context = modifier(current_context)

            yield MessageUpdate(new_context=current_context)
        else:
            async for update in _run_tools_serially(
                batch.blocks,
                assistant_messages,
                can_use_tool,
                current_context,
            ):
                if update.new_context:
                    current_context = update.new_context
                yield MessageUpdate(message=update.message, new_context=current_context)


def _extract_modifier(ctx_mod: Any) -> tuple[str, Any]:
    """Read ``tool_use_id`` + ``modify_context`` from either a dict or a
    ``ContextModifier`` dataclass."""
    if isinstance(ctx_mod, dict):
        return (
            ctx_mod.get("tool_use_id", ""),
            ctx_mod.get("modify_context"),
        )
    return (
        getattr(ctx_mod, "tool_use_id", ""),
        getattr(ctx_mod, "modify_context", None),
    )


async def _run_tools_serially(
    tool_use_messages: list[ToolUseBlock],
    assistant_messages: list[AssistantMessage],
    can_use_tool: Any,
    tool_use_context: ToolContext,
) -> AsyncGenerator[MessageUpdate, None]:
    from src.services.tool_execution.tool_execution import run_tool_use

    current_context = tool_use_context

    for tool_use in tool_use_messages:
        if current_context.set_in_progress_tool_use_ids:
            current_context.set_in_progress_tool_use_ids(
                lambda prev: prev | {tool_use.id}
            )

        assistant_msg = _find_assistant_message(tool_use, assistant_messages)

        async for update in run_tool_use(
            tool_use,
            assistant_msg,
            can_use_tool,
            current_context,
        ):
            msg = update.get("message") if isinstance(update, dict) else getattr(update, "message", None)
            ctx_mod = update.get("context_modifier") if isinstance(update, dict) else getattr(update, "context_modifier", None)

            if ctx_mod:
                modify_fn = ctx_mod.get("modify_context") if isinstance(ctx_mod, dict) else getattr(ctx_mod, "modify_context", None)
                if modify_fn:
                    current_context = modify_fn(current_context)

            yield MessageUpdate(message=msg, new_context=current_context)

        _mark_tool_use_as_complete(current_context, tool_use.id)


async def _run_tools_concurrently(
    tool_use_messages: list[ToolUseBlock],
    assistant_messages: list[AssistantMessage],
    can_use_tool: Any,
    tool_use_context: ToolContext,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run a concurrent-safe batch.

    Mirrors TS ``runToolsConcurrently`` (toolOrchestration.ts:152): up to
    ``MAX_TOOL_USE_CONCURRENCY`` tools run in parallel; results are
    yielded as they arrive.

    Errors that escape ``run_tool_use`` are surfaced as synthetic
    tool_use_error messages — silently dropping them produces an
    unmatched tool_use block and the next API turn 400s.
    """
    from src.services.tool_execution.tool_execution import run_tool_use
    from src.types.messages import create_user_message

    max_concurrency = _get_max_tool_use_concurrency()
    semaphore = asyncio.Semaphore(max_concurrency)
    results_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    pending = len(tool_use_messages)

    async def _run_single(tool_use: ToolUseBlock) -> None:
        nonlocal pending
        try:
            async with semaphore:
                if tool_use_context.set_in_progress_tool_use_ids:
                    tool_use_context.set_in_progress_tool_use_ids(
                        lambda prev: prev | {tool_use.id}
                    )
                assistant_msg = _find_assistant_message(tool_use, assistant_messages)
                try:
                    async for update in run_tool_use(
                        tool_use,
                        assistant_msg,
                        can_use_tool,
                        tool_use_context,
                    ):
                        await results_queue.put(_normalize_update(update))
                except Exception as exc:
                    # run_tool_use already wraps its own errors — anything
                    # that escapes is a real bug. Don't swallow silently;
                    # surface it as a tool_use_error so the model has a
                    # matching tool_result block and the operator sees
                    # the failure.
                    logger.exception(
                        "Unhandled error in concurrent tool %s (%s)",
                        tool_use.name, tool_use.id,
                    )
                    err_msg = create_user_message(
                        content=[{
                            "type": "tool_result",
                            "content": (
                                f"<tool_use_error>Error: {exc}"
                                "</tool_use_error>"
                            ),
                            "is_error": True,
                            "tool_use_id": tool_use.id,
                        }],
                        toolUseResult=f"Error: {exc}",
                    )
                    await results_queue.put({
                        "message": err_msg,
                        "context_modifier": None,
                    })
        finally:
            _mark_tool_use_as_complete(tool_use_context, tool_use.id)
            pending -= 1
            if pending == 0:
                await results_queue.put(None)

    tasks = [
        asyncio.ensure_future(_run_single(tool_use))
        for tool_use in tool_use_messages
    ]

    try:
        while True:
            item = await results_queue.get()
            if item is None:
                break
            yield item
    finally:
        # Best-effort cleanup if the consumer aborts early.
        for task in tasks:
            if not task.done():
                task.cancel()


def _normalize_update(update: Any) -> dict[str, Any]:
    if isinstance(update, dict):
        msg = update.get("message")
        ctx_mod = update.get("context_modifier")
    else:
        msg = getattr(update, "message", None)
        ctx_mod = getattr(update, "context_modifier", None)
    return {"message": msg, "context_modifier": ctx_mod}


def _find_assistant_message(
    tool_use: ToolUseBlock,
    assistant_messages: list[AssistantMessage],
) -> AssistantMessage:
    for msg in assistant_messages:
        content = msg.content if hasattr(msg, "content") else []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "tool_use" and block.get("id") == tool_use.id:
                        return msg
    if assistant_messages:
        return assistant_messages[-1]
    from src.types.messages import AssistantMessage as AM
    return AM(role="assistant", content=[])
