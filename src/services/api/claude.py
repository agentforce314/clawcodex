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


def add_cache_breakpoints(
    messages: list[dict[str, Any]],
    *,
    enable_prompt_caching: bool = True,
    skip_cache_write: bool = False,
) -> list[dict[str, Any]]:
    """Place exactly one ``cache_control`` marker on the last message.

    Mirrors the load-bearing invariant of TS ``addCacheBreakpoints``
    (``services/api/claude.ts:3107``): one and only one ``cache_control``
    marker per request, attached to the last block of the marker message.
    Two markers would extend a doomed cache prefix by one turn and waste
    storage on mycro's KVCC; zero markers leave every multi-turn
    conversation re-billing its history at full rate.

    The marker lands on the last message by default. When ``skip_cache_write``
    is True it shifts to the second-to-last message — the fire-and-forget
    fork pattern from TS line 3127-3132. With fewer than two messages and
    ``skip_cache_write=True`` the call is a graceful no-op (no negative
    indexing).

    The mycro-internal ``useCachedMC`` / ``cache_edits`` / ``cache_reference``
    machinery from TS is intentionally omitted — those are backend-specific
    features with no public API contract.

    The function never mutates its input. The marker message is shallow-
    cloned, its content list is shallow-cloned, and the final block is
    shallow-cloned before ``cache_control`` is attached. Earlier messages
    and earlier blocks in the marker message are passed through by
    reference so the cost is O(1) extra allocations regardless of history
    length.

    Args:
        messages: API-shape message list (each ``{"role": ..., "content": ...}``).
        enable_prompt_caching: When False, returns ``messages`` unchanged
            (round-1 follow-up: model-aware disable lives elsewhere; this
            is the request-level kill switch).
        skip_cache_write: When True, place the marker on the SECOND-to-last
            message.

    Returns:
        A new list with at most one message shallow-cloned. The marker is a
        plain ``{"type": "ephemeral"}`` dict — TTL/scope upgrades happen on
        the system-prompt array, not at the message level.
    """
    if not enable_prompt_caching:
        return messages
    if not messages:
        return list(messages)

    marker_index = len(messages) - 2 if skip_cache_write else len(messages) - 1
    if marker_index < 0:
        # skip_cache_write with one message — graceful no-op.
        return list(messages)

    out: list[dict[str, Any]] = list(messages)
    msg = out[marker_index]
    content = msg.get("content")
    cache_control = {"type": "ephemeral"}

    if isinstance(content, str):
        new_content: list[dict[str, Any]] = [
            {"type": "text", "text": content, "cache_control": cache_control}
        ]
    elif isinstance(content, list):
        if not content:
            # Empty block list — wrap an empty text block so the marker
            # has somewhere to live. Matches TS, which always emits at
            # least one block for the marker message.
            new_content = [
                {"type": "text", "text": "", "cache_control": cache_control}
            ]
        else:
            new_content = list(content[:-1])
            last_block = content[-1]
            if isinstance(last_block, dict):
                cloned_block = dict(last_block)
                cloned_block["cache_control"] = cache_control
            else:
                cloned_block = {
                    "type": "text",
                    "text": str(last_block),
                    "cache_control": cache_control,
                }
            new_content.append(cloned_block)
    else:
        # Unknown content shape (None, int, ...). Coerce to a single text
        # block so we still emit a marker. This matches TS's "always wrap"
        # behaviour for stringy content.
        new_content = [
            {
                "type": "text",
                "text": "" if content is None else str(content),
                "cache_control": cache_control,
            }
        ]

    cloned_msg = dict(msg)
    cloned_msg["content"] = new_content
    out[marker_index] = cloned_msg
    return out


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
    # Round-2 (ch04): message-level prompt caching.
    # Mirrors TS ``enablePromptCaching`` / ``skipCacheWrite`` args to
    # ``addCacheBreakpoints``. Default True matches TS production default.
    enable_prompt_caching: bool = True
    skip_cache_write: bool = False


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

        # Round-2 (ch04): attach one message-level cache_control marker so
        # multi-turn conversations benefit from server-side ephemeral
        # prompt caching. See ``add_cache_breakpoints`` docstring for the
        # invariant being maintained.
        api_messages = add_cache_breakpoints(
            messages,
            enable_prompt_caching=opts.enable_prompt_caching,
            skip_cache_write=opts.skip_cache_write,
        )

        # Pre-API image validation: reject any base64 image larger than
        # Anthropic's 5 MB limit before the round trip. Surfaces as a
        # readable error event so the model/user sees what happened
        # instead of a generic API failure. Mirrors TS
        # validateImagesForAPI invocation at utils/imageValidation.ts.
        #
        # TODO: only the Anthropic provider path runs this check.
        # src/providers/{openai_compatible,anthropic_provider,minimax_provider}.py
        # all bypass it. Promote validation into BaseProvider._prepare_messages
        # if/when those providers grow image-content-block support.
        try:
            from src.utils.image_validation import ImageSizeError, validate_images_for_api
            validate_images_for_api(api_messages)
        except ImageSizeError as e:
            yield ErrorEvent(error=str(e))
            return

        create_kwargs: dict[str, Any] = {
            "model": opts.model,
            "max_tokens": opts.max_tokens,
            "messages": api_messages,
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
