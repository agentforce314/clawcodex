"""Tool orchestration — mirrors TypeScript toolOrchestration.ts.

Two modes:
- Mode 1: Streaming — use StreamingToolExecutor (called from query loop during streaming)
- Mode 2: Batch — run_tools() with partition_tool_calls() for batching
  consecutive concurrency-safe tools
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator

from src.services.tool_execution.streaming_executor import (
    MessageUpdate,
    ToolUseBlock,
    _mark_tool_use_as_complete,
)
from src.tool_system.build_tool import find_tool_by_name

if TYPE_CHECKING:
    from src.tool_system.build_tool import Tools
    from src.tool_system.context import ToolContext
    from src.types.messages import AssistantMessage


def _get_max_tool_use_concurrency() -> int:
    try:
        return int(os.environ.get("CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY", "10"))
    except (ValueError, TypeError):
        return 10


@dataclass
class Batch:
    is_concurrency_safe: bool
    blocks: list[ToolUseBlock]


def partition_tool_calls(
    tool_use_messages: list[ToolUseBlock],
    tool_use_context: ToolContext,
) -> list[Batch]:
    batches: list[Batch] = []
    for tool_use in tool_use_messages:
        tool = find_tool_by_name(tool_use_context.options.tools, tool_use.name)
        is_concurrency_safe = False
        if tool is not None:
            try:
                is_concurrency_safe = bool(tool.is_concurrency_safe(tool_use.input))
            except Exception:
                is_concurrency_safe = False

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
            queued_context_modifiers: dict[str, list[Any]] = {}
            async for update in _run_tools_concurrently(
                batch.blocks,
                assistant_messages,
                can_use_tool,
                current_context,
            ):
                msg = update.get("message") if isinstance(update, dict) else getattr(update, "message", None)
                ctx_mod = update.get("context_modifier") if isinstance(update, dict) else getattr(update, "context_modifier", None)

                if ctx_mod:
                    tool_use_id = ctx_mod.get("tool_use_id", "") if isinstance(ctx_mod, dict) else getattr(ctx_mod, "tool_use_id", "")
                    modify_fn = ctx_mod.get("modify_context") if isinstance(ctx_mod, dict) else getattr(ctx_mod, "modify_context", None)
                    if tool_use_id not in queued_context_modifiers:
                        queued_context_modifiers[tool_use_id] = []
                    if modify_fn:
                        queued_context_modifiers[tool_use_id].append(modify_fn)

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
    from src.services.tool_execution.tool_execution import run_tool_use

    max_concurrency = _get_max_tool_use_concurrency()
    semaphore = asyncio.Semaphore(max_concurrency)
    results_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    active_count = len(tool_use_messages)

    async def _run_single(tool_use: ToolUseBlock) -> None:
        nonlocal active_count
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
                    await results_queue.put(update if isinstance(update, dict) else {"message": getattr(update, "message", None), "context_modifier": getattr(update, "context_modifier", None)})
            except Exception:
                pass
            finally:
                _mark_tool_use_as_complete(tool_use_context, tool_use.id)
                active_count -= 1
                if active_count == 0:
                    await results_queue.put(None)

    for tool_use in tool_use_messages:
        asyncio.ensure_future(_run_single(tool_use))

    while True:
        item = await results_queue.get()
        if item is None:
            break
        yield item


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
