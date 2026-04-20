from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from ..services.api.claude import (
    CallModelOptions,
    ContentBlockStop,
    ErrorEvent,
    MessageDelta,
    MessageStart,
    MessageStop,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
    ToolUseStart,
    UsageEvent,
    call_model,
)
from ..services.api.claude import StreamEvent as APIStreamEvent
from ..services.api.errors import PromptTooLongError
from ..services.api.logging import NonNullableUsage, update_usage
from ..tool_system.build_tool import Tool, find_tool_by_name
from ..tool_system.context import ToolContext
from .config import QueryConfig

logger = logging.getLogger(__name__)

MAX_REACTIVE_COMPACT_RETRIES = 3


@dataclass
class QueryTurn:
    turn_number: int = 0
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    text_content: str = ""
    thinking_content: str = ""
    stop_reason: str = ""
    usage: NonNullableUsage = field(default_factory=NonNullableUsage)


@dataclass
class StreamingQueryState:
    messages: list[dict[str, Any]]
    system_prompt: str
    tools: list[Tool]
    context: ToolContext
    config: QueryConfig
    turn_count: int = 0
    total_usage: NonNullableUsage = field(default_factory=NonNullableUsage)
    compact_retries: int = 0
    is_done: bool = False


@dataclass
class QueryEvent:
    type: str
    data: Any = None


async def streaming_query(
    messages: list[dict[str, Any]],
    system_prompt: str,
    tools: list[Tool],
    context: ToolContext,
    config: QueryConfig | None = None,
    *,
    abort_signal: Any | None = None,
    client: Any | None = None,
    on_stop_hooks: Any | None = None,
    compact_fn: Any | None = None,
    on_tool_uses: Any | None = None,
) -> AsyncGenerator[QueryEvent, None]:
    cfg = config or QueryConfig()
    state = StreamingQueryState(
        messages=list(messages),
        system_prompt=system_prompt,
        tools=tools,
        context=context,
        config=cfg,
    )

    while not state.is_done and state.turn_count < cfg.max_turns:
        if abort_signal and getattr(abort_signal, "aborted", False):
            yield QueryEvent(type="aborted")
            return

        state.turn_count += 1
        turn = QueryTurn(turn_number=state.turn_count)

        yield QueryEvent(type="turn_start", data={"turn": state.turn_count})

        try:
            async for event in _run_model_turn(state, turn, client):
                yield event
        except PromptTooLongError as e:
            if cfg.reactive_compact_enabled and state.compact_retries < MAX_REACTIVE_COMPACT_RETRIES:
                state.compact_retries += 1
                yield QueryEvent(type="reactive_compact", data={
                    "retry": state.compact_retries,
                    "token_gap": e.token_gap,
                })
                if compact_fn:
                    try:
                        compacted = await compact_fn(state.messages, state.system_prompt)
                        if compacted:
                            state.messages = compacted
                            continue
                    except Exception as compact_err:
                        logger.error("Compact failed: %s", compact_err)
                yield QueryEvent(type="error", data={"error": str(e)})
                state.is_done = True
                return
            else:
                yield QueryEvent(type="error", data={"error": str(e)})
                state.is_done = True
                return

        update_usage(state.total_usage, turn.usage)

        if turn.stop_reason in ("end_turn", "stop_sequence", "") and not turn.tool_uses:
            state.is_done = True
            yield QueryEvent(type="turn_complete", data={
                "turn": state.turn_count,
                "stop_reason": turn.stop_reason,
                "text": turn.text_content,
            })

            if cfg.stop_hooks_enabled and on_stop_hooks:
                try:
                    await on_stop_hooks(state.messages)
                except Exception as e:
                    logger.warning("Stop hooks error: %s", e)

            break

        if turn.tool_uses:
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": []}
            if turn.text_content:
                assistant_msg["content"].append({"type": "text", "text": turn.text_content})
            for tu in turn.tool_uses:
                assistant_msg["content"].append({
                    "type": "tool_use",
                    "id": tu["id"],
                    "name": tu["name"],
                    "input": tu["input"],
                })
            state.messages.append(assistant_msg)

            yield QueryEvent(type="tool_execution_start", data={
                "tool_uses": turn.tool_uses,
            })

            if on_tool_uses:
                tool_results = await on_tool_uses(turn.tool_uses, tools, context)
                if tool_results:
                    state.messages.append({
                        "role": "user",
                        "content": tool_results,
                    })
                    yield QueryEvent(type="tool_results_appended", data={
                        "count": len(tool_results),
                    })

            if cfg.stop_hooks_enabled and on_stop_hooks:
                try:
                    await on_stop_hooks(state.messages)
                except Exception as e:
                    logger.warning("Stop hooks error: %s", e)

            continue

        state.is_done = True
        yield QueryEvent(type="turn_complete", data={
            "turn": state.turn_count,
            "stop_reason": turn.stop_reason,
            "text": turn.text_content,
        })

    yield QueryEvent(type="query_complete", data={
        "turns": state.turn_count,
        "total_usage": state.total_usage.to_dict(),
    })


async def _run_model_turn(
    state: StreamingQueryState,
    turn: QueryTurn,
    client: Any = None,
) -> AsyncGenerator[QueryEvent, None]:
    options = CallModelOptions(
        model=state.config.model,
        max_tokens=state.config.max_tokens,
        system_prompt=state.system_prompt,
        tools=state.tools,
        thinking_enabled=state.config.thinking_enabled,
        thinking_budget=state.config.thinking_budget,
        effort=state.config.effort,
        temperature=state.config.temperature,
        stop_sequences=state.config.stop_sequences,
        structured_output=state.config.structured_output,
        extra_headers=state.config.extra_headers,
        extra_body=state.config.extra_body,
    )

    current_tool_use: dict[str, Any] | None = None
    tool_use_json_parts: list[str] = []

    async for event in call_model(state.messages, options, client):
        if isinstance(event, MessageStart):
            yield QueryEvent(type="message_start", data={
                "model": event.model,
            })

        elif isinstance(event, TextDelta):
            turn.text_content += event.text
            yield QueryEvent(type="text", data={"text": event.text})

        elif isinstance(event, ThinkingDelta):
            turn.thinking_content += event.text
            yield QueryEvent(type="thinking", data={"text": event.text})

        elif isinstance(event, ToolUseStart):
            current_tool_use = {
                "id": event.id,
                "name": event.name,
                "input": {},
            }
            tool_use_json_parts = []
            yield QueryEvent(type="tool_use_start", data={
                "id": event.id,
                "name": event.name,
            })

        elif isinstance(event, ToolUseDelta):
            tool_use_json_parts.append(event.partial_json)

        elif isinstance(event, ContentBlockStop):
            if current_tool_use:
                full_json = "".join(tool_use_json_parts)
                try:
                    current_tool_use["input"] = json.loads(full_json) if full_json else {}
                except json.JSONDecodeError:
                    current_tool_use["input"] = {}
                turn.tool_uses.append(current_tool_use)
                current_tool_use = None
                tool_use_json_parts = []

        elif isinstance(event, MessageDelta):
            turn.stop_reason = event.stop_reason
            update_usage(turn.usage, event.usage)

        elif isinstance(event, UsageEvent):
            update_usage(turn.usage, event.usage)

        elif isinstance(event, ErrorEvent):
            yield QueryEvent(type="error", data={"error": event.error})

        elif isinstance(event, MessageStop):
            pass
