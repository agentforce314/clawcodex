from __future__ import annotations

import logging
import os
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

# Per chapter §"Output Token Cap": production p99 output is ~4.9K tokens, so
# the typical 32K/64K defaults over-reserve slot capacity by 8-16×. Default
# cap is 8K; requests that hit it retry once at 64K (non-streaming fallback
# — lands in a later PR).
CAPPED_DEFAULT_MAX_TOKENS = 8_000
MAX_NON_STREAMING_TOKENS = 64_000

# Per-model "native" upper limits. Used to floor the default at the model's
# own default when it is below the cap (e.g. Haiku is already 8K). Mirrors
# TS ``getModelMaxOutputTokens()``.
_MODEL_DEFAULT_MAX_TOKENS: dict[str, int] = {
    "claude-opus-4-7": 64_000,
    "claude-opus-4-6": 64_000,
    "claude-opus-4-5": 64_000,
    "claude-opus-4-1": 32_000,
    "claude-opus-4-0": 32_000,
    "claude-sonnet-4-6": 64_000,
    "claude-sonnet-4-5": 64_000,
    "claude-sonnet-4-0": 32_000,
    "claude-haiku-4-5": 8_000,
    "claude-3-5-sonnet-20241022": 8_000,
    "claude-3-5-haiku-20241022": 8_000,
    "claude-3-opus-20240229": 4_000,
}

CLIENT_REQUEST_ID_HEADER = "x-client-request-id"


def get_max_output_tokens_for_model(model: str) -> int:
    """Return the effective output-token cap for ``model``.

    Mirrors TS ``getMaxOutputTokensForModel`` (claude.ts:3443-3463).
    The default is ``min(per_model_native_default, 8_000)``; the env var
    ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` overrides bounded by the per-model
    upper limit. Returns at least 1 (a zero-token request would 400).

    The 8K cap exists for slot-reservation economics — see chapter for the
    p99 distribution. Requests that hit the cap retry once at
    ``MAX_NON_STREAMING_TOKENS`` (64K) via the non-streaming fallback path
    (wired in a later PR).
    """
    native_default = _MODEL_DEFAULT_MAX_TOKENS.get(model, CAPPED_DEFAULT_MAX_TOKENS)
    default = min(native_default, CAPPED_DEFAULT_MAX_TOKENS)
    upper_limit = max(native_default, CAPPED_DEFAULT_MAX_TOKENS)

    env_override = os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "").strip()
    if env_override:
        try:
            override_val = int(env_override)
            if override_val > 0:
                return max(1, min(override_val, upper_limit))
        except ValueError:
            pass

    return max(1, default)


def adjust_params_for_non_streaming(
    max_tokens: int,
    thinking_budget: int | None,
    *,
    cap: int = MAX_NON_STREAMING_TOKENS,
) -> tuple[int, int | None]:
    """Cap ``max_tokens`` and (if needed) ``thinking.budget_tokens``.

    Mirrors TS ``adjustParamsForNonStreaming`` (claude.ts:3408-3436). The
    API requires ``max_tokens > thinking.budget_tokens`` so the budget
    shrinks to ``capped_max - 1`` when both are at the ceiling.

    Returns (capped_max_tokens, adjusted_thinking_budget).
    """
    capped = min(max_tokens, cap)
    if thinking_budget is not None and thinking_budget >= capped:
        thinking_budget = max(1, capped - 1)
    return capped, thinking_budget


def make_client_request_id() -> str:
    """Generate a fresh UUID for x-client-request-id correlation.

    Mirrors TS ``randomUUID()`` usage at the client construction site
    (services/api/client.ts:540-542). The header lets the API team
    correlate timeouts (where the server never returns a request ID) with
    server-side logs.
    """
    return uuid.uuid4().hex


def _is_first_party_endpoint(client: Any) -> bool:
    """True iff ``client`` targets the first-party Anthropic endpoint.

    Mirrors TS ``isFirstPartyAnthropicBaseUrl()`` (utils/model/providers.ts).
    Third-party providers (Bedrock, Vertex, Foundry, OpenAI-shim) reject
    unknown headers, so ``x-client-request-id`` is only emitted on the
    first-party path.

    The Anthropic Python SDK stores its base URL on ``client.base_url`` after
    construction. When the caller leaves it default, the SDK's internal URL
    is the first-party endpoint, so an empty / missing attribute also
    qualifies as first-party.
    """
    base_url = getattr(client, "base_url", "") or ""
    if not base_url:
        return True
    return "anthropic.com" in str(base_url).lower()


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
    """Per-call configuration for ``call_model``.

    ``max_tokens=None`` defers to ``get_max_output_tokens_for_model()`` so
    the slot-reservation cap (8K default) applies. Pass an explicit int
    only when the caller needs a specific ceiling.
    """

    model: str = "claude-sonnet-4-6"
    max_tokens: int | None = None
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

    def resolved_max_tokens(self) -> int:
        """Return ``max_tokens`` with the per-model cap applied.

        ``None`` (default) routes through the helper for the slot-
        reservation cap. Explicit positive ints are honoured but bounded at
        ``MAX_NON_STREAMING_TOKENS`` so callers cannot configure a request
        the API will reject.
        """
        if self.max_tokens is None:
            return get_max_output_tokens_for_model(self.model)
        return max(1, min(self.max_tokens, MAX_NON_STREAMING_TOKENS))


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
            "max_tokens": opts.resolved_max_tokens(),
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

        # Build the per-request header dict so we can inject
        # ``x-client-request-id`` without mutating the caller's options.
        # Explicit caller headers win — if the caller pre-set the request
        # ID (e.g. to thread one across a streaming + non-streaming-fallback
        # pair, landing in a later PR) we honour that.
        extra_headers = dict(opts.extra_headers or {})
        if (
            _is_first_party_endpoint(client)
            and CLIENT_REQUEST_ID_HEADER not in extra_headers
        ):
            extra_headers[CLIENT_REQUEST_ID_HEADER] = make_client_request_id()
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
