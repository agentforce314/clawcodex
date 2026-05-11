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
# cap is 8K; requests that hit it retry once at 64K (non-streaming fallback).
CAPPED_DEFAULT_MAX_TOKENS = 8_000
MAX_NON_STREAMING_TOKENS = 64_000

# Per-model "native" upper limits. Used to floor the default at the model's
# own default when it is below the cap (e.g. Haiku is already 8K). Mirrors
# TS ``getModelMaxOutputTokens()``.
_MODEL_DEFAULT_MAX_TOKENS: dict[str, int] = {
    # Claude 4.x — opus and sonnet support large outputs natively
    "claude-opus-4-7": 64_000,
    "claude-opus-4-6": 64_000,
    "claude-opus-4-5": 64_000,
    "claude-opus-4-1": 32_000,
    "claude-opus-4-0": 32_000,
    "claude-sonnet-4-6": 64_000,
    "claude-sonnet-4-5": 64_000,
    "claude-sonnet-4-0": 32_000,
    "claude-haiku-4-5": 8_000,
    # Claude 3.x
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
    ``MAX_NON_STREAMING_TOKENS`` (64K) via the non-streaming fallback path.
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


def add_cache_breakpoints(
    messages: list[dict[str, Any]],
    *,
    enable_prompt_caching: bool = True,
    skip_cache_write: bool = False,
) -> list[dict[str, Any]]:
    """Place exactly one ``cache_control`` marker on the conversation tail.

    Mirrors TS ``addCacheBreakpoints`` (claude.ts:3107-3255), simplified to
    omit the cache-edits / cache-references machinery (mycro internal — not
    part of the public API contract). Per chapter §"Three Tiers", exactly
    ONE marker per request: two markers create false-positive cache
    extensions that get evicted on the next turn anyway and waste a
    breakpoint slot.

    Args:
        messages: API-shaped message list (``role``/``content`` dicts).
            Not mutated — returns a shallow-copied list.
        enable_prompt_caching: When False, returns the input unchanged
            (e.g. third-party providers that reject ``cache_control``).
        skip_cache_write: For fire-and-forget forks (subagents that
            discard their tail), move the marker one position earlier so
            the cache entry already exists upstream — the fork doesn't
            leave its own tail in the KVCC. Mirrors TS at claude.ts:3133.

    Returns the new list. Messages strictly before the marker are
    untouched; the marker message is shallow-cloned with ``cache_control``
    attached to its LAST content block (string contents wrap into a
    single text block).
    """
    if not enable_prompt_caching or not messages:
        return list(messages)

    # Mirror TS exactly: ``markerIndex = messages.length - 1`` (or -2 for
    # skip_cache_write). When ``skip_cache_write`` is True and the list has
    # only one message, ``markerIndex`` becomes -1 and the marker never
    # lands — the fire-and-forget fork has no useful cache prefix anyway
    # (the only message IS the tail being discarded). Mirrors TS at
    # claude.ts:3133 where ``markerIndex = -1`` results in no message
    # matching ``index === markerIndex`` inside the .map().
    marker_idx = len(messages) - (2 if skip_cache_write else 1)
    if marker_idx < 0:
        return list(messages)

    out: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        if idx != marker_idx:
            out.append(msg)
            continue

        cloned = dict(msg)
        content = cloned.get("content")
        if isinstance(content, str):
            cloned["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        elif isinstance(content, list) and content:
            # Mark the LAST block (which is where the API places the
            # cache boundary for that message). Don't mutate the caller's
            # list — clone the affected block and reassemble.
            new_content = list(content[:-1])
            tail = content[-1]
            if isinstance(tail, dict):
                tail_clone = dict(tail)
            else:
                tail_clone = {"type": "text", "text": str(tail)}
            tail_clone["cache_control"] = {"type": "ephemeral"}
            new_content.append(tail_clone)
            cloned["content"] = new_content
        else:
            # No content — nothing useful to cache. Leave unchanged.
            pass
        out.append(cloned)
    return out


SMALL_FAST_MODEL = "claude-haiku-4-5"


async def query_haiku(
    *,
    user_prompt: str,
    system_prompt: str = "",
    signal: Any = None,
    client: Any = None,
    model: str = SMALL_FAST_MODEL,
    structured_output: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> Any:
    """Streamlined non-streaming entry point for classifiers / titles / compact.

    Mirrors TS ``queryHaiku`` (claude.ts:3285-3335). Skips streaming, tool
    search, thinking, advisor, agentic plumbing. Returns the raw SDK
    response so the caller can extract ``.content[0].text`` (or whatever
    shape its task needs) without going through stream-event aggregation.

    The chapter rationale: not every API call needs the full pipeline.
    Compaction, session-title generation, side-question classifiers — each
    of these uses Haiku to do one synchronous transformation, and the
    streaming plumbing is pure overhead.
    """
    if client is None:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed — install optional deps "
                "to use query_haiku"
            ) from exc
        client = anthropic.AsyncAnthropic()

    effective_max_tokens = (
        max_tokens
        if max_tokens is not None
        else get_max_output_tokens_for_model(model)
    )

    create_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": effective_max_tokens,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    if system_prompt:
        create_kwargs["system"] = [{"type": "text", "text": system_prompt}]

    extra_headers: dict[str, str] = {}
    if _is_first_party_endpoint(client):
        extra_headers[CLIENT_REQUEST_ID_HEADER] = make_client_request_id()
    if extra_headers:
        create_kwargs["extra_headers"] = extra_headers

    if structured_output:
        # Mirrors TS ``output_config.format`` for structured outputs
        # (claude.ts:1615-1624).
        create_kwargs["output_config"] = {"format": structured_output}

    return await client.messages.create(**create_kwargs)


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


@dataclass
class RetryEvent:
    """Surface retry progress as a stream event.

    Yielded by ``call_model`` when ``with_retry_stream`` is waiting between
    attempts. The UI uses ``attempt`` and ``delay_ms`` to render a "Server
    overloaded, retrying in 5s..." message inline with the rest of the
    stream — same shape as TS ``SystemAPIErrorMessage`` (utils/messages.ts).

    ``model`` is the model the *next* attempt will use (may differ from the
    original if a model fallback fired).
    """
    type: Literal["retry"] = "retry"
    attempt: int = 0
    delay_ms: int = 0
    error_type: str = ""
    status: int | None = None
    message: str = ""
    model: str = ""


@dataclass
class FallbackEvent:
    """Yielded when the streaming attempt fell back to non-streaming.

    Mirrors TS ``tengu_streaming_fallback_to_non_streaming`` event +
    ``onStreamingFallback`` callback. The UI uses this to hide "streaming"
    chrome (cursors, partial-text animations) since the next chunks are
    actually a synthetic replay of a single non-streamed response.
    """
    type: Literal["fallback"] = "fallback"
    cause: str = ""
    request_id: str | None = None


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
    RetryEvent,
    FallbackEvent,
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
    """Per-call configuration for ``call_model`` and friends.

    Default values are tuned for the chapter's invariants:
    - ``max_tokens=None`` defers to ``get_max_output_tokens_for_model()`` so
      the slot-reservation cap (8K default) applies. Pass an explicit int
      only when the caller needs a specific ceiling.
    - ``enable_prompt_caching=False`` is the conservative default — most
      ad-hoc callers (tests, internal classifiers) don't want cache_control
      blocks. The production streaming pipeline sets it to True via
      ``QueryConfig.enable_prompt_caching``.
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
    enable_prompt_caching: bool = False
    skip_cache_write: bool = False

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


def _stream_fallback_disabled() -> bool:
    """True iff the non-streaming fallback is opt-out via env var.

    Mirrors TS ``CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK`` (claude.ts:2513).
    Set this when re-executing the request would double-fire side effects
    (e.g. streaming tool execution where the partial stream already started
    a tool before the failure).
    """
    raw = os.environ.get("CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _build_create_kwargs(
    *,
    messages: list[dict[str, Any]],
    opts: "CallModelOptions",
    client: Any,
    max_tokens: int,
    thinking_budget: int | None,
) -> dict[str, Any]:
    """Compose the ``client.messages.create`` keyword arguments.

    Factored out so the streaming and non-streaming paths share one source
    of truth for params: the only difference is the ``stream`` flag and the
    capped ``max_tokens``/``thinking_budget`` (per
    ``adjust_params_for_non_streaming``).
    """
    system_blocks = _build_system_blocks(opts.system_prompt)
    tool_schemas = _build_tool_schemas(opts.tools)

    # Phase E: place exactly one cache_control marker on the tail of the
    # conversation when caching is enabled. add_cache_breakpoints returns a
    # new list and clones the marker message, so messages-from-the-caller
    # are not mutated.
    api_messages = (
        add_cache_breakpoints(
            messages,
            enable_prompt_caching=True,
            skip_cache_write=opts.skip_cache_write,
        )
        if opts.enable_prompt_caching
        else messages
    )

    create_kwargs: dict[str, Any] = {
        "model": opts.model,
        "max_tokens": max_tokens,
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
    if opts.thinking_enabled and thinking_budget is not None and thinking_budget > 0:
        create_kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget,
        }

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

    return create_kwargs


def _events_from_non_streaming_response(
    response: Any,
    fallback_model: str,
) -> list[StreamEvent]:
    """Synthesize a stream-shaped event sequence from a non-streamed response.

    The non-streaming fallback returns a single ``Message``-shaped object;
    the caller's downstream code (``query/streaming.py``) is structured
    around event-shaped chunks. To keep both paths uniform, replay the
    response as if it had been streamed: ``message_start`` → per-block
    text/tool deltas → ``message_delta`` (stop_reason) → ``message_stop``.

    Mirrors TS ``claude.ts:2615-2638`` which builds an ``AssistantMessage``
    from the non-streamed result. The Python port emits stream events
    rather than constructing a message because the downstream pipeline
    aggregates events back into a message anyway.
    """
    events: list[StreamEvent] = []
    usage_attr = getattr(response, "usage", None)
    start_usage = NonNullableUsage(
        input_tokens=getattr(usage_attr, "input_tokens", 0) if usage_attr else 0,
        output_tokens=0,
        cache_creation_input_tokens=(
            getattr(usage_attr, "cache_creation_input_tokens", 0) if usage_attr else 0
        ),
        cache_read_input_tokens=(
            getattr(usage_attr, "cache_read_input_tokens", 0) if usage_attr else 0
        ),
    )
    events.append(MessageStart(
        model=getattr(response, "model", fallback_model),
        usage=start_usage,
    ))

    content = getattr(response, "content", None) or []
    for index, block in enumerate(content):
        block_type = getattr(block, "type", "text")
        if block_type == "text":
            text_val = getattr(block, "text", "") or ""
            if text_val:
                events.append(TextDelta(text=text_val, index=index))
            events.append(ContentBlockStop(index=index))
        elif block_type == "tool_use":
            events.append(ToolUseStart(
                id=getattr(block, "id", ""),
                name=getattr(block, "name", ""),
                index=index,
            ))
            input_obj = getattr(block, "input", None)
            if input_obj is not None:
                import json as _json
                try:
                    partial = _json.dumps(input_obj)
                except (TypeError, ValueError):
                    partial = "{}"
                events.append(ToolUseDelta(partial_json=partial, index=index))
            events.append(ContentBlockStop(index=index))
        elif block_type == "thinking":
            think_text = getattr(block, "thinking", "") or ""
            if think_text:
                events.append(ThinkingDelta(text=think_text, index=index))
            events.append(ContentBlockStop(index=index))
        else:
            events.append(ContentBlockStop(index=index))

    output_tokens = getattr(usage_attr, "output_tokens", 0) if usage_attr else 0
    stop_reason = getattr(response, "stop_reason", "") or ""
    events.append(MessageDelta(
        stop_reason=stop_reason,
        usage=NonNullableUsage(output_tokens=output_tokens),
    ))
    events.append(MessageStop())
    return events


async def _execute_non_streaming_request(
    messages: list[dict[str, Any]],
    opts: "CallModelOptions",
    client: Any,
    max_tokens: int,
) -> Any:
    """Synchronous (non-streaming) ``messages.create`` for fallback recovery.

    Mirrors TS ``executeNonStreamingRequest`` (claude.ts:833-936). Caps
    ``max_tokens`` at ``MAX_NON_STREAMING_TOKENS`` (64K) and adjusts the
    thinking budget to satisfy ``max_tokens > thinking.budget_tokens``.
    """
    capped_max, capped_thinking = adjust_params_for_non_streaming(
        max_tokens,
        thinking_budget=opts.thinking_budget if opts.thinking_enabled else None,
    )
    create_kwargs = _build_create_kwargs(
        messages=messages,
        opts=opts,
        client=client,
        max_tokens=capped_max,
        thinking_budget=capped_thinking,
    )
    # No stream=True — explicit absence keeps the SDK on the sync path.
    return await client.messages.create(**create_kwargs)


async def call_model(
    messages: list[dict[str, Any]],
    options: CallModelOptions | None = None,
    client: Any = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Stream a model response, with idle-watchdog + non-streaming fallback.

    Per chapter §"Streaming" and §"Non-Streaming Fallback", this is the
    recovery contract Claude Code is built on. If the streaming body stalls
    for ``CLAUDE_STREAM_IDLE_TIMEOUT_MS`` (default 90 s) or raises before
    completing, fall back to ``messages.create(stream=False)`` and replay
    the response as a stream-shaped event sequence so downstream consumers
    see the same shape either way.

    Fallback is suppressed when ``CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK``
    is set (chapter §"Non-Streaming Fallback"): when streaming tool
    execution is active, re-executing the request would re-fire tools.
    """
    from src.utils.stream_watchdog import AsyncStreamWatchdog

    opts = options or CallModelOptions()
    start_time = time.time()
    accumulated_usage = NonNullableUsage()
    call_log = APICallLog(model=opts.model, start_time=start_time)
    max_tokens = opts.resolved_max_tokens()

    try:
        if client is None:
            try:
                import anthropic
                client = anthropic.AsyncAnthropic()
            except ImportError:
                yield ErrorEvent(error="anthropic package not installed")
                return

        create_kwargs = _build_create_kwargs(
            messages=messages,
            opts=opts,
            client=client,
            max_tokens=max_tokens,
            thinking_budget=opts.thinking_budget if opts.thinking_enabled else None,
        )
        create_kwargs["stream"] = True

        stream = None
        watchdog: AsyncStreamWatchdog | None = None
        streaming_exception: Exception | None = None
        try:
            stream = await client.messages.create(**create_kwargs)
            watchdog = AsyncStreamWatchdog(stream)
            watchdog.arm()
        except Exception as exc:
            # Stream-creation failure (e.g. 404, network refused, 401, auth
            # rejected before the body opens). Falls through to the fallback
            # path below — non-streaming may succeed for proxies that 404
            # only on the streaming endpoint.
            streaming_exception = exc

        if stream is not None and streaming_exception is None:
            try:
                async for event in _drain_stream(
                    stream, watchdog, opts, accumulated_usage, call_log,
                ):
                    yield event
            except Exception as exc:
                streaming_exception = exc
            finally:
                if watchdog is not None:
                    watchdog.disarm()

        if streaming_exception is None and (
            watchdog is None or not watchdog.fired
        ):
            # Stream completed cleanly — emit the trailing usage event.
            yield UsageEvent(usage=accumulated_usage)
            return

        if streaming_exception is not None and is_prompt_too_long_error(
            streaming_exception
        ):
            actual, limit = parse_prompt_too_long_token_counts(str(streaming_exception))
            raise PromptTooLongError(
                message=str(streaming_exception),
                actual_tokens=actual,
                limit_tokens=limit,
            ) from streaming_exception

        if _stream_fallback_disabled():
            # Caller has opted out of the fallback — surface the original
            # error rather than re-executing.
            if streaming_exception is not None:
                yield ErrorEvent(error=str(streaming_exception))
            else:
                yield ErrorEvent(error="Stream idle timeout")
            return

        cause = (
            "watchdog"
            if (watchdog is not None and watchdog.fired)
            else "stream_error"
        )
        yield FallbackEvent(cause=cause)

        # Critical: the streaming path may already have updated
        # ``accumulated_usage`` from a partial ``message_start`` (and possibly
        # ``message_delta``) event before the watchdog fired. The non-
        # streaming fallback returns the *complete* response, and the synthetic
        # event loop below will add the full usage figures. Without this
        # reset, input_tokens (and cache_creation / cache_read fields) would
        # be summed twice and the trailing UsageEvent would report inflated
        # totals. Mirrors TS at claude.ts:2616-2638 which builds a fresh
        # AssistantMessage from the non-streamed result rather than merging
        # with the partial state.
        accumulated_usage.input_tokens = 0
        accumulated_usage.output_tokens = 0
        accumulated_usage.cache_creation_input_tokens = 0
        accumulated_usage.cache_read_input_tokens = 0
        call_log.stop_reason = ""

        try:
            response = await _execute_non_streaming_request(
                messages, opts, client, max_tokens,
            )
        except Exception as fallback_exc:
            call_log.error = str(fallback_exc)
            if is_prompt_too_long_error(fallback_exc):
                actual, limit = parse_prompt_too_long_token_counts(
                    str(fallback_exc),
                )
                raise PromptTooLongError(
                    message=str(fallback_exc),
                    actual_tokens=actual,
                    limit_tokens=limit,
                ) from fallback_exc
            yield ErrorEvent(error=str(fallback_exc))
            return

        for ev in _events_from_non_streaming_response(response, opts.model):
            # Mirror the accumulated_usage updates the streaming path does
            # so the trailing UsageEvent matches.
            if isinstance(ev, MessageStart) and ev.usage:
                update_usage(accumulated_usage, ev.usage)
            elif isinstance(ev, MessageDelta) and ev.usage:
                update_usage(accumulated_usage, ev.usage)
                call_log.stop_reason = ev.stop_reason
            yield ev

    finally:
        call_log.end_time = time.time()
        call_log.usage = accumulated_usage
        log_api_call(call_log)

    # Only reached on the fallback-success path. The clean-stream branch
    # ``return``s after emitting UsageEvent at line 706; the fallback-disabled
    # and fallback-error branches likewise return early. This trailing yield
    # is the single emission point for the fallback-success case.
    yield UsageEvent(usage=accumulated_usage)


async def _drain_stream(
    stream: Any,
    watchdog: Any,
    opts: "CallModelOptions",
    accumulated_usage: NonNullableUsage,
    call_log: APICallLog,
) -> AsyncGenerator[StreamEvent, None]:
    """Iterate the SDK stream and yield ``StreamEvent`` objects.

    Extracted from ``call_model`` so the streaming and fallback paths can
    be reasoned about independently. Resets the watchdog on every chunk so
    a stalled body trips the idle timeout instead of the per-request one.

    Exceptions propagate to the caller — ``call_model`` translates them
    into a fallback decision.
    """
    block_index = 0
    async for event in stream:
        if watchdog is not None:
            watchdog.reset()
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
