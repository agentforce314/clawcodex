from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Literal, Union

from .errors import (
    PromptTooLongError,
    is_prompt_too_long_error,
    parse_prompt_too_long_token_counts,
)
from .logging import APICallLog, NonNullableUsage, log_api_call, update_usage

logger = logging.getLogger(__name__)

CLI_SYSPROMPT_PREFIX = "[S]"


@dataclass
class TextDelta:
    type: Literal["text_delta"] = "text_delta"
    text: str = ""
    index: int = 0


@dataclass
class ToolUseStart:
    type: Literal["tool_use_start"] = "tool_use_start"
    id: str = ""
    name: str = ""
    index: int = 0


@dataclass
class ToolUseDelta:
    type: Literal["tool_use_delta"] = "tool_use_delta"
    partial_json: str = ""
    index: int = 0


@dataclass
class ToolUseEnd:
    type: Literal["tool_use_end"] = "tool_use_end"
    index: int = 0


@dataclass
class ThinkingDelta:
    type: Literal["thinking_delta"] = "thinking_delta"
    text: str = ""
    index: int = 0


@dataclass
class MessageStart:
    type: Literal["message_start"] = "message_start"
    model: str = ""
    usage: NonNullableUsage = field(default_factory=NonNullableUsage)


@dataclass
class MessageDelta:
    type: Literal["message_delta"] = "message_delta"
    stop_reason: str = ""
    usage: NonNullableUsage = field(default_factory=NonNullableUsage)


@dataclass
class MessageStop:
    type: Literal["message_stop"] = "message_stop"


@dataclass
class ContentBlockStop:
    type: Literal["content_block_stop"] = "content_block_stop"
    index: int = 0


@dataclass
class UsageEvent:
    type: Literal["usage"] = "usage"
    usage: NonNullableUsage = field(default_factory=NonNullableUsage)


@dataclass
class ErrorEvent:
    type: Literal["error"] = "error"
    error: str = ""


StreamEvent = Union[
    TextDelta,
    ToolUseStart,
    ToolUseDelta,
    ToolUseEnd,
    ThinkingDelta,
    MessageStart,
    MessageDelta,
    MessageStop,
    ContentBlockStop,
    UsageEvent,
    ErrorEvent,
]


def split_system_prompt_prefix(system_prompt: str) -> tuple[str, str]:
    if system_prompt.startswith(CLI_SYSPROMPT_PREFIX):
        return CLI_SYSPROMPT_PREFIX, system_prompt[len(CLI_SYSPROMPT_PREFIX):].lstrip()
    return "", system_prompt


def tool_to_api_schema(tool: Any) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description({}) if callable(tool.description) else str(tool.description),
        "input_schema": dict(tool.input_schema) if tool.input_schema else {"type": "object", "properties": {}},
    }
    return schema


def _build_system_blocks(system_prompt: str) -> list[dict[str, Any]]:
    if not system_prompt:
        return []
    _, content = split_system_prompt_prefix(system_prompt)
    return [{"type": "text", "text": content}]


def _build_tool_schemas(tools: list[Any]) -> list[dict[str, Any]]:
    return [tool_to_api_schema(t) for t in tools]


@dataclass
class CallModelOptions:
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 16384
    system_prompt: str = ""
    tools: list[Any] = field(default_factory=list)
    thinking_enabled: bool = False
    thinking_budget: int = 10000
    effort: str = "high"
    temperature: float | None = None
    stop_sequences: list[str] | None = None
    structured_output: dict[str, Any] | None = None
    extra_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None


async def call_model(
    messages: list[dict[str, Any]],
    options: CallModelOptions | None = None,
    client: Any = None,
) -> AsyncGenerator[StreamEvent, None]:
    opts = options or CallModelOptions()
    start_time = time.time()
    accumulated_usage = NonNullableUsage()
    call_log = APICallLog(model=opts.model, start_time=start_time)

    try:
        if client is None:
            try:
                import anthropic
                client = anthropic.AsyncAnthropic()
            except ImportError:
                yield ErrorEvent(error="anthropic package not installed")
                return

        system_blocks = _build_system_blocks(opts.system_prompt)
        tool_schemas = _build_tool_schemas(opts.tools)

        create_kwargs: dict[str, Any] = {
            "model": opts.model,
            "max_tokens": opts.max_tokens,
            "messages": messages,
        }

        if system_blocks:
            create_kwargs["system"] = system_blocks

        if tool_schemas:
            create_kwargs["tools"] = tool_schemas

        if opts.temperature is not None:
            create_kwargs["temperature"] = opts.temperature

        if opts.stop_sequences:
            create_kwargs["stop_sequences"] = opts.stop_sequences

        if opts.thinking_enabled:
            create_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": opts.thinking_budget,
            }

        extra_headers = dict(opts.extra_headers or {})
        if extra_headers:
            create_kwargs["extra_headers"] = extra_headers

        if opts.extra_body:
            create_kwargs.update(opts.extra_body)

        create_kwargs["stream"] = True

        stream = await client.messages.create(**create_kwargs)

        block_index = 0
        async for event in stream:
            event_type = getattr(event, "type", "")

            if event_type == "message_start":
                msg = getattr(event, "message", None)
                if msg:
                    usage_data = getattr(msg, "usage", None)
                    model_name = getattr(msg, "model", opts.model)
                    if usage_data:
                        start_usage = NonNullableUsage(
                            input_tokens=getattr(usage_data, "input_tokens", 0),
                            output_tokens=getattr(usage_data, "output_tokens", 0),
                            cache_creation_input_tokens=getattr(usage_data, "cache_creation_input_tokens", 0),
                            cache_read_input_tokens=getattr(usage_data, "cache_read_input_tokens", 0),
                        )
                        update_usage(accumulated_usage, start_usage)
                        yield MessageStart(model=model_name, usage=start_usage)
                    else:
                        yield MessageStart(model=model_name)

            elif event_type == "content_block_start":
                cb = getattr(event, "content_block", None)
                idx = getattr(event, "index", block_index)
                if cb:
                    cb_type = getattr(cb, "type", "")
                    if cb_type == "tool_use":
                        yield ToolUseStart(
                            id=getattr(cb, "id", ""),
                            name=getattr(cb, "name", ""),
                            index=idx,
                        )
                    elif cb_type == "thinking":
                        pass
                block_index = idx + 1

            elif event_type == "content_block_delta":
                delta = getattr(event, "delta", None)
                idx = getattr(event, "index", 0)
                if delta:
                    delta_type = getattr(delta, "type", "")
                    if delta_type == "text_delta":
                        yield TextDelta(text=getattr(delta, "text", ""), index=idx)
                    elif delta_type == "input_json_delta":
                        yield ToolUseDelta(partial_json=getattr(delta, "partial_json", ""), index=idx)
                    elif delta_type == "thinking_delta":
                        yield ThinkingDelta(text=getattr(delta, "thinking", ""), index=idx)

            elif event_type == "content_block_stop":
                idx = getattr(event, "index", 0)
                yield ContentBlockStop(index=idx)

            elif event_type == "message_delta":
                delta = getattr(event, "delta", None)
                usage_data = getattr(event, "usage", None)
                stop_reason = ""
                if delta:
                    stop_reason = getattr(delta, "stop_reason", "") or ""
                delta_usage = NonNullableUsage()
                if usage_data:
                    delta_usage = NonNullableUsage(
                        output_tokens=getattr(usage_data, "output_tokens", 0),
                    )
                    update_usage(accumulated_usage, delta_usage)
                call_log.stop_reason = stop_reason
                yield MessageDelta(stop_reason=stop_reason, usage=delta_usage)

            elif event_type == "message_stop":
                yield MessageStop()

    except Exception as error:
        error_msg = str(error)
        call_log.error = error_msg

        if is_prompt_too_long_error(error):
            actual, limit = parse_prompt_too_long_token_counts(error_msg)
            raise PromptTooLongError(
                message=error_msg,
                actual_tokens=actual,
                limit_tokens=limit,
            ) from error

        yield ErrorEvent(error=error_msg)
    finally:
        call_log.end_time = time.time()
        call_log.usage = accumulated_usage
        log_api_call(call_log)

    yield UsageEvent(usage=accumulated_usage)
