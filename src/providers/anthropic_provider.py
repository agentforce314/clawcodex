"""Anthropic provider implementation."""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Generator, Optional, Any, TYPE_CHECKING
from urllib.parse import urlparse
from uuid import uuid4

from .base import BaseProvider, ChatResponse, MessageInput, TextChunkCallback

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from src.utils.abort_controller import AbortSignal


# WI-4.4 (ch17 Phase 4): defer the ``import anthropic`` call. The SDK
# alone is ~150-200ms to import (verified by ``my-docs/profiler-baseline.md``:
# provider import accounts for ~70% of cold-start time). Cold-start paths
# that don't make API calls (``clawcodex --version``, ``clawcodex config``,
# fast-path subcommands) shouldn't pay that cost.
#
# Module-level ``__getattr__`` (PEP 562) provides the ``anthropic``
# attribute lazily so existing test patterns like
# ``@patch("src.providers.anthropic_provider.anthropic.Anthropic")``
# keep working without modification. The first attribute access (a test
# patch or ``_ensure_client``) triggers the SDK import; subsequent
# accesses hit the cached value in ``globals()``.


def __getattr__(name: str):
    """PEP 562 module-level __getattr__: lazy-load the anthropic SDK.

    Triggered on any access to an unbound module-level attribute. We
    only handle ``"anthropic"`` here; other names raise the standard
    AttributeError so typos still fail loudly.
    """
    if name == "anthropic":
        try:
            import anthropic as _module
        except ModuleNotFoundError:  # pragma: no cover
            class _MissingAnthropic:
                class Anthropic:  # type: ignore[no-redef]
                    def __init__(self, *args, **kwargs):
                        raise ModuleNotFoundError(
                            "anthropic package is not installed. "
                            "Install optional dependencies to use AnthropicProvider."
                        )
            _module = _MissingAnthropic()  # type: ignore[assignment]
        globals()["anthropic"] = _module
        return _module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _extract_usage_dict(usage: Any) -> dict[str, Any]:
    """Build the ChatResponse.usage dict from an Anthropic SDK ``Usage`` object.

    WI-0.2 (ch17 Phase 0): forwards prompt-cache credits and the
    ``cache_creation`` 5m/1h breakdown so downstream consumers stop reading
    0 from the dict. Mirrors TS ``services/api/claude.ts``'s usage handling
    (chapter line 61: "Token counting is anchored on the API's actual
    ``usage`` field ... accounting for prompt caching credits").

    The chapter calls out four observability fields on the API response:
      * ``cache_creation_input_tokens`` — top-level int.
      * ``cache_read_input_tokens`` — top-level int.
      * ``cache_creation.ephemeral_5m_input_tokens`` — sub-object.
      * ``cache_creation.ephemeral_1h_input_tokens`` — sub-object.

    Note on thinking tokens: the Anthropic Python SDK 0.88.0 ``Usage`` type
    does NOT expose a thinking-token attribute (verified via
    ``Usage.__annotations__``). Extended-thinking tokens live in content
    blocks, not ``usage``, so they are not forwarded here. Extend this
    helper if a future SDK adds the attribute.
    """
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }

    result: dict[str, Any] = {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
    }

    # cache_creation breakdown — sub-object with ephemeral_5m / ephemeral_1h.
    # Forwarded as a nested dict so consumers can attribute cache writes by TTL.
    cache_creation = getattr(usage, "cache_creation", None)
    if cache_creation is not None:
        result["cache_creation"] = {
            "ephemeral_5m_input_tokens": getattr(cache_creation, "ephemeral_5m_input_tokens", 0) or 0,
            "ephemeral_1h_input_tokens": getattr(cache_creation, "ephemeral_1h_input_tokens", 0) or 0,
        }

    return result


# Claude 4.x and newer models support a much larger ``max_tokens`` ceiling
# than the SDK's historical 4096 default — opus-4.x and sonnet-4.x accept
# up to 32K (and up to 64K with the larger-output beta header). The legacy
# 4096 default routinely truncated long completions on these models, which
# in turn caused agent loops to either re-prompt to finish a half-written
# patch or accept the truncated body verbatim. Bumping to 32K matches
# Anthropic's documented standard ceiling for 4.x without requiring a
# beta opt-in.
#
# Older Claude 3.x snapshots cap at 4K-8K depending on tier; pulling the
# default up to 32K would make the API reject those requests with a 400.
# Detection is by model-name pattern so newer 4.x point releases
# (e.g. ``claude-opus-4-7-20260201``) opt in automatically.
_LARGE_MAX_TOKENS_MODEL_PATTERN = re.compile(
    r"claude-(?:sonnet|opus|haiku)-(?:4-\d+|[5-9]\b|\d{2,})",
    re.IGNORECASE,
)

DEFAULT_MAX_OUTPUT_TOKENS_4X = 32000
DEFAULT_MAX_OUTPUT_TOKENS_LEGACY = 4096


def _default_max_tokens(model: str | None) -> int:
    """Pick a sensible ``max_tokens`` ceiling for the given model.

    Returns 32K for the Claude 4.x family (which is what the API allows
    without beta opt-ins), and 4096 for everything else (preserves the
    legacy ceiling on 3.x where the API rejects higher values).

    A caller can always override via ``kwargs["max_tokens"]``; this only
    affects the default when no value is supplied.
    """
    if model and _LARGE_MAX_TOKENS_MODEL_PATTERN.search(model):
        return DEFAULT_MAX_OUTPUT_TOKENS_4X
    return DEFAULT_MAX_OUTPUT_TOKENS_LEGACY


class AnthropicProvider(BaseProvider):
    """Anthropic Claude provider."""

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        """Initialize Anthropic provider.

        Args:
            api_key: Anthropic API key
            base_url: Base URL (optional)
            model: Default model (default: claude-sonnet-4-6)
        """
        super().__init__(api_key, base_url, model or "claude-sonnet-4-6")

        self._client_kwargs = {"api_key": api_key}
        if base_url:
            self._client_kwargs["base_url"] = base_url
        # ch16 round-4 — ANTHROPIC_CUSTOM_HEADERS (enterprise gateway/proxy
        # auth) → default_headers on the Anthropic client.
        from src.services.api.custom_headers import get_anthropic_custom_headers
        _headers = get_anthropic_custom_headers()
        if _headers:
            self._client_kwargs["default_headers"] = _headers
        self.client = None

    def _ensure_client(self):
        if self.client is not None:
            return self.client
        # WI-4.4: resolve ``anthropic`` through the module's globals so
        # test patches at ``src.providers.anthropic_provider.anthropic.Anthropic``
        # are visible. The first access triggers the PEP 562
        # ``__getattr__`` lazy-load above.
        mod = sys.modules[__name__]
        self.client = mod.anthropic.Anthropic(**self._client_kwargs)
        return self.client

    def _effective_base_url(self) -> str | None:
        """The base URL the SDK will actually use.

        ch04 round-3 G2 (critic-corrected first-party check): the
        Anthropic SDK falls back to the ``ANTHROPIC_BASE_URL`` env var
        when the constructor ``base_url`` is None — a constructor-only
        check would treat env-configured proxies as first-party (the
        exact incident class TS guards in ``providers.ts:120-135``).
        """
        return self._client_kwargs.get("base_url") or os.environ.get(
            "ANTHROPIC_BASE_URL"
        ) or None

    def _is_first_party(self) -> bool:
        """True iff requests go to Anthropic's own endpoint.

        No effective base URL (SDK default) = first-party; otherwise the
        parsed host must be ``api.anthropic.com`` (TS
        ``isFirstPartyAnthropicBaseUrl``, providers.ts:120-135).
        """
        effective = self._effective_base_url()
        if not effective:
            return True
        try:
            return urlparse(effective).hostname == "api.anthropic.com"
        except Exception:
            return False

    def _request_id_headers(self) -> dict[str, str]:
        """Per-request ``x-client-request-id`` for first-party endpoints.

        TS ``buildFetch`` (client.ts:556-589): a timeout never gets a
        server-assigned request id, so the client-side UUID is the only
        way to correlate it with server logs. First-party only —
        third-party providers / strict proxies may reject unknown
        headers.
        """
        if self._is_first_party():
            return {"x-client-request-id": str(uuid4())}
        return {}

    def has_custom_endpoint(self) -> bool:
        """True iff requests target a non-first-party endpoint.

        WI-2.3 (ch17 Phase 2): used by ``cache_state.is_first_party_provider``
        to decide whether ``scope: 'global'`` may be emitted on
        ``cache_control`` blocks (only valid against Anthropic's first-party
        endpoint; proxies / self-hosted / Bedrock shims would either 400
        or silently drop the field). ch04 round-3 hardening: consults the
        EFFECTIVE base URL (constructor -> ANTHROPIC_BASE_URL env), so an
        env-configured proxy is also treated as custom — same incident
        class as the request-id check above.
        """
        return not self._is_first_party()

    def _build_chat_response(self, response: Any) -> ChatResponse:
        """Convert Anthropic SDK response into the shared ChatResponse shape.

        Beyond the text + ``tool_use`` projection used by the rest of
        the agentic loop, we also preserve advisor server-tool blocks
        (``server_tool_use`` with ``name=='advisor'`` and the
        ``advisor_tool_result`` follow-up). They must round-trip through
        conversation history so that on the next turn the API sees a
        valid use/result pair; stripping them here would either lose the
        advisor's analysis or — worse — leave an orphan ``server_tool_use``
        that the API rejects on replay. See ``src/utils/advisor.py``.

        The SDK parses unknown discriminator values (e.g.
        ``advisor_tool_result``) leniently via ``construct_type`` — the
        block lands as the first matching union variant (typically
        ``BetaTextBlock``) but the original fields (``type``,
        ``tool_use_id``, ``content``) are preserved on the instance.
        ``model_dump()`` round-trips them as a dict for downstream replay.
        """
        content_text = ""
        tool_uses: list[dict[str, Any]] = []
        raw_content_blocks: list[dict[str, Any]] = []

        for block in response.content:
            block_type = getattr(block, "type", "text")
            block_name = getattr(block, "name", None)
            is_advisor = block_type == "advisor_tool_result" or (
                block_type == "server_tool_use" and block_name == "advisor"
            )
            if is_advisor:
                dump = getattr(block, "model_dump", None)
                if callable(dump):
                    raw_content_blocks.append(dict(dump(exclude_none=True)))
                else:
                    # Fallback for non-Pydantic shapes (test doubles, etc.).
                    raw_content_blocks.append(
                        {k: v for k, v in vars(block).items() if v is not None}
                    )
                continue
            if block_type == "text":
                text_val = getattr(block, "text", "")
                if text_val is not None:
                    content_text += str(text_val)
            elif block_type == "tool_use":
                tool_uses.append({
                    "id": str(getattr(block, "id", "")),
                    "name": str(getattr(block, "name", "")),
                    "input": dict(getattr(block, "input", {})),
                })

        usage = getattr(response, "usage", None)
        return ChatResponse(
            content=content_text,
            model=getattr(response, "model", self.model or ""),
            usage=_extract_usage_dict(usage),
            finish_reason=str(getattr(response, "stop_reason", "stop")),
            tool_uses=tool_uses if tool_uses else None,
            raw_content_blocks=raw_content_blocks or None,
        )

    def _client_for_request(self, kwargs: dict[str, Any]):
        """Resolve the client, honoring a per-call ``sdk_max_retries``.

        ch04 round-3 G3 condition (c): the manual 529 retry lane in the
        query loop passes ``sdk_max_retries=0`` so the SDK's default
        2 auto-retries don't stack underneath it (~9 wire attempts and a
        lying attempt counter — TS sets maxRetries: 0 for the same
        reason, claude.ts:1798). Direct callers (compaction, advisor,
        title) keep the SDK default, preserving their silent resilience
        to 429/connection blips.
        """
        client = self._ensure_client()
        sdk_max_retries = kwargs.pop("sdk_max_retries", None)
        if sdk_max_retries is not None:
            try:
                return client.with_options(max_retries=int(sdk_max_retries))
            except Exception:
                # SDK auto-retries silently re-enable under the manual
                # lane in this (unexpected) case -- make it observable.
                logger.debug(
                    "with_options(max_retries=%s) failed; SDK default "
                    "retries remain active under the retry lane",
                    sdk_max_retries,
                    exc_info=True,
                )
                return client
        return client

    def _merge_request_id(self, kwargs: dict[str, Any]) -> str | None:
        """Inject the per-request id into kwargs' extra_headers (G2)."""
        headers = self._request_id_headers()
        if not headers:
            return None
        merged = dict(kwargs.get("extra_headers") or {})
        merged.setdefault("x-client-request-id", headers["x-client-request-id"])
        kwargs["extra_headers"] = merged
        return merged["x-client-request-id"]

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> ChatResponse:
        """Synchronous chat completion.

        Args:
            messages: List of chat messages
            tools: Optional list of tool schemas
            **kwargs: Additional parameters (model, max_tokens, temperature, etc.)

        Returns:
            Chat response
        """
        model = self._get_model(**kwargs)
        max_tokens = kwargs.get("max_tokens", _default_max_tokens(model))

        system = kwargs.pop("system", None)

        # Convert messages to Anthropic format
        anthropic_messages = self._prepare_messages(messages)

        # Make API call
        client = self._client_for_request(kwargs)
        extra_kwargs: dict[str, Any] = {}
        if tools:
            extra_kwargs["tools"] = tools
        self._merge_request_id(kwargs)

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=anthropic_messages,
            **({"system": system} if system else {}),
            **extra_kwargs,
            **{k: v for k, v in kwargs.items() if k not in ["model", "max_tokens", "tools"]},
        )

        return self._build_chat_response(response)

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        """Streaming chat completion.

        Args:
            messages: List of chat messages
            tools: Optional list of tool schemas
            **kwargs: Additional parameters

        Yields:
            Chunks of response content
        """
        model = self._get_model(**kwargs)
        max_tokens = kwargs.get("max_tokens", _default_max_tokens(model))

        # Convert messages
        anthropic_messages = self._prepare_messages(messages)

        # Stream API call
        client = self._ensure_client()
        extra_kwargs: dict[str, Any] = {}
        if tools:
            extra_kwargs["tools"] = tools

        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            messages=anthropic_messages,
            **extra_kwargs,
            **{k: v for k, v in kwargs.items() if k not in ["model", "max_tokens", "tools"]},
        ) as stream:
            for text in stream.text_stream:
                yield text

    def chat_stream_response(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        on_text_chunk: TextChunkCallback | None = None,
        abort_signal: "AbortSignal | None" = None,
        on_thinking_chunk: TextChunkCallback | None = None,
        **kwargs
    ) -> ChatResponse:
        """Stream Anthropic text chunks and return the final structured response.

        WI-5.2: wraps the stream with a ``StreamWatchdog`` that closes the
        underlying HTTP response if no chunks arrive within
        ``CLAUDE_STREAM_IDLE_TIMEOUT_MS`` (default 90 s). On timeout the
        iterator raises; we catch it and fall back to the non-streaming
        ``chat()`` path so the user gets an answer rather than a hung session.

        ESC-cancellation: when ``abort_signal`` is provided, a listener is
        registered that calls ``stream.response.close()`` when the signal
        fires. The close interrupts the SDK's blocking socket read so the
        ``for text in stream.text_stream`` iterator raises immediately —
        without it, ESC during a tool-use-only response (no intervening
        text chunks for ``on_text_chunk`` to observe) waits for the model
        to finish generating before the outer query loop can bail. We
        translate the raise into ``AbortError`` so callers can distinguish
        a user-initiated cancel from the watchdog's idle-timeout fallback.
        """
        from src.utils.stream_watchdog import StreamWatchdog

        from ._stream_abort import StreamAbortGuard

        guard = StreamAbortGuard(abort_signal)
        # Fast-path: if abort fired before we even build the request,
        # raise directly so the caller's cancel boundary unwinds at
        # the same place the mid-stream path lands.
        guard.raise_if_pre_aborted()

        model = self._get_model(**kwargs)
        max_tokens = kwargs.get("max_tokens", _default_max_tokens(model))
        system = kwargs.pop("system", None)
        anthropic_messages = self._prepare_messages(messages)
        # ch04 round-4 GAP A — one cache_control marker on the last message
        # (TS addCacheBreakpoints, claude.ts:3078/1719). Without it the
        # canonical prefix cache ends at the system blocks and every
        # multi-turn request re-bills the full conversation history at
        # input rate. Streaming (agentic) lane only: one-shot internal
        # chat() calls (compaction summarize, watchdog fallback re-issue)
        # stay unmarked — a cache WRITE on a never-reused prefix costs a
        # 25% premium for nothing (TS queryHaiku ships
        # enablePromptCaching:false for the same reason). Budget: ≤3
        # system markers + this 1 = the API's 4-marker cap. Deliberate
        # asymmetry (critic m2): MinimaxProvider is NOT a subclass, so its
        # anthropic-compat endpoint gets system-block markers only (the
        # pre-existing behavior) and no message marker — conservative
        # until its cache_control-on-messages support is verified.
        from src.services.api.claude import add_cache_breakpoints

        anthropic_messages = add_cache_breakpoints(anthropic_messages)

        client = self._client_for_request(kwargs)
        extra_kwargs: dict[str, Any] = {}
        if tools:
            extra_kwargs["tools"] = tools
        request_id = self._merge_request_id(kwargs)

        def _fallback_to_chat() -> ChatResponse:
            """Re-issue the request without streaming (WI-5.2 recovery path).

            Mirrors TS ``streamLatencyWatchdog.ts:resumeViaChatCompletion``.
            Strips kwargs that ``chat`` already accepts as named args so we
            don't double-pass them.
            """
            forwarded = {
                k: v
                for k, v in kwargs.items()
                if k not in ["model", "max_tokens", "tools"]
            }
            # NB ``forwarded`` retains extra_headers with the STREAM's
            # x-client-request-id; chat()'s setdefault keeps it, so the
            # hung stream and its recovery request correlate under ONE id
            # (deliberate; TS supports caller-pre-set ids the same way).
            return self.chat(
                messages,
                tools=tools,
                **({"system": system} if system else {}),
                **forwarded,
                model=model,
                max_tokens=max_tokens,
            )

        streamed_text = ""
        watchdog_fired = False
        final_message = None
        try:
            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                messages=anthropic_messages,
                **({"system": system} if system else {}),
                **extra_kwargs,
                **{k: v for k, v in kwargs.items() if k not in ["model", "max_tokens", "tools"]},
            ) as stream, guard.attach(stream):
                # ``guard.attach`` registered the close-on-abort listener
                # (see ``_stream_abort.py`` for the race-safe ordering
                # and the close-via-stream.response.close mechanism).
                # The provider keeps the watchdog and fallback logic
                # local: they aren't abort-related.
                watchdog = StreamWatchdog(stream, request_id=request_id)
                watchdog.arm()
                try:
                    # Iterate the FULL event stream rather than the
                    # text-only ``stream.text_stream`` view. With extended
                    # thinking enabled (Anthropic Claude 4.x), the model
                    # often emits 60–120s+ of ``thinking_delta`` events
                    # with no intervening ``text_delta``s while it works
                    # through a hard prompt. ``text_stream`` only yields
                    # on text deltas, so the watchdog (default 90s idle)
                    # would fire and close the stream mid-thinking — the
                    # provider then falls back to non-streaming ``chat()``
                    # which the Anthropic SDK rejects with
                    # "Streaming is required for operations that may take
                    # longer than 10 minutes."
                    #
                    # Resetting the watchdog on EVERY event type
                    # (thinking deltas, input_json deltas, message_start /
                    # _stop, content_block_start / _stop, message_delta)
                    # keeps the stream alive as long as the model is
                    # genuinely producing output. We still only accumulate
                    # text into ``streamed_text`` and fire ``on_text_chunk``
                    # for ``text_delta`` events — thinking is private and
                    # must not be surfaced to the visible output channel.
                    for event in stream:
                        # Each event — text, thinking, tool, snapshot —
                        # is liveness; push the deadline forward.
                        watchdog.reset()
                        delta = getattr(event, "delta", None)
                        if delta is None:
                            continue
                        # Only ``TextDelta`` carries a ``text`` field.
                        # ``ThinkingDelta`` has ``thinking`` (deliberately
                        # not surfaced — it's the private scratchpad).
                        # ``InputJSONDelta`` has ``partial_json`` (handled
                        # by the SDK's final-message accumulation, no
                        # need to track here).
                        # Thinking deltas → a SEPARATE channel (not the visible
                        # text output). Surfaced live for the TUI's thinking view,
                        # mirroring how the final thinking block is already shown.
                        thinking = getattr(delta, "thinking", None)
                        if thinking and on_thinking_chunk is not None:
                            on_thinking_chunk(thinking)
                        text = getattr(delta, "text", None)
                        if not text:
                            continue
                        streamed_text += text
                        if on_text_chunk is not None:
                            on_text_chunk(text)
                    try:
                        final_message = stream.get_final_message()
                    except Exception:
                        final_message = None
                finally:
                    # Snapshot watchdog state INSIDE the finally so it
                    # survives an exception propagating through the
                    # iterator (close() raises mid-stream). Critic B1
                    # caught this — otherwise the assignment was on a
                    # line never reached during the exception path and
                    # the fallback branch below ran with watchdog_fired
                    # still False.
                    watchdog_fired = watchdog.fired
                    watchdog.disarm()
        except Exception as streaming_exc:
            # Abort path FIRST: a user cancel must win over the
            # watchdog fallback (the abort listener may also have
            # tripped the watchdog's race, so we'd otherwise route a
            # user cancel through non-streaming recovery and burn
            # another round-trip).
            guard.reraise_if_aborted(streaming_exc)

            # WI-5.2 fallback path: stream interrupted by the idle
            # watchdog. Fall back to non-streaming so the user still
            # gets an answer. If the failure is something else
            # (network/auth/etc.), re-raise the original.
            if watchdog_fired:
                logger.warning(
                    "stream idle watchdog fired; falling back to "
                    "non-streaming (x-client-request-id=%s)",
                    request_id or "n/a",
                )
                try:
                    return _fallback_to_chat()
                except Exception as fallback_exc:
                    # Recovery itself failed — surface BOTH causes so
                    # observers see the original streaming error AND the
                    # fallback failure that prevented recovery. Critic
                    # M3 — bare ``except: pass`` swallowed the fallback
                    # error and re-raised only the streaming one.
                    raise fallback_exc from streaming_exc
            raise

        # Stream completed normally but abort may have fired between
        # ``stream.__exit__`` and here. Surface it at the same boundary
        # the mid-stream path uses.
        guard.raise_if_post_aborted()

        if watchdog_fired:
            # Stream got interrupted but no exception escaped the
            # with-block (close-side raced the iterator's normal exit).
            # Fall back to non-streaming for the full answer.
            return _fallback_to_chat()

        if final_message is not None:
            return self._build_chat_response(final_message)

        return ChatResponse(
            content=streamed_text,
            model=model,
            usage={},
            finish_reason="stop",
            tool_uses=None,
        )

    def get_available_models(self) -> list[str]:
        """Get list of available Anthropic models.

        Returns:
            List of model names
        """
        return [
            # Claude 4 series (latest)
            "claude-sonnet-4-6",
            "claude-sonnet-4-5",
            "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-0",
            "claude-sonnet-4-20250514",
            "claude-opus-4-6",
            "claude-opus-4-5",
            "claude-opus-4-5-20251101",
            "claude-opus-4-1",
            "claude-opus-4-1-20250805",
            "claude-opus-4-0",
            "claude-opus-4-20250514",
            "claude-haiku-4-5",
            "claude-haiku-4-5-20251001",
            # Legacy
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ]
