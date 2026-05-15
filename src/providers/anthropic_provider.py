"""Anthropic provider implementation."""

from __future__ import annotations

import sys
from typing import Generator, Optional, Any, TYPE_CHECKING

from .base import BaseProvider, ChatResponse, MessageInput, TextChunkCallback

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

    def has_custom_endpoint(self) -> bool:
        """True iff the caller passed a non-default ``base_url``.

        WI-2.3 (ch17 Phase 2): used by ``cache_state.is_first_party_provider``
        to decide whether ``scope: 'global'`` may be emitted on
        ``cache_control`` blocks (only valid against Anthropic's first-party
        endpoint; proxies / self-hosted / Bedrock shims would either 400
        or silently drop the field). Public API so the cache-state module
        doesn't read ``self._client_kwargs`` (encapsulation).
        """
        return bool(self._client_kwargs.get("base_url"))

    def _build_chat_response(self, response: Any) -> ChatResponse:
        """Convert Anthropic SDK response into the shared ChatResponse shape."""
        content_text = ""
        tool_uses: list[dict[str, Any]] = []

        for block in response.content:
            block_type = getattr(block, "type", "text")
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
        )

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
        max_tokens = kwargs.get("max_tokens", 4096)

        system = kwargs.pop("system", None)

        # Convert messages to Anthropic format
        anthropic_messages = self._prepare_messages(messages)

        # Make API call
        client = self._ensure_client()
        extra_kwargs: dict[str, Any] = {}
        if tools:
            extra_kwargs["tools"] = tools

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
        max_tokens = kwargs.get("max_tokens", 4096)

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
        from src.utils.abort_controller import AbortError
        from src.utils.stream_watchdog import StreamWatchdog

        # Fast-path: if abort fired before we even build the request, don't
        # spend the round-trip — raise directly so the caller's cancel
        # boundary unwinds at the same place the mid-stream path lands.
        if abort_signal is not None and abort_signal.aborted:
            raise AbortError(abort_signal.reason or "user_interrupt")

        model = self._get_model(**kwargs)
        max_tokens = kwargs.get("max_tokens", 4096)
        system = kwargs.pop("system", None)
        anthropic_messages = self._prepare_messages(messages)

        client = self._ensure_client()
        extra_kwargs: dict[str, Any] = {}
        if tools:
            extra_kwargs["tools"] = tools

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
        abort_listener: Any = None
        try:
            with client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                messages=anthropic_messages,
                **({"system": system} if system else {}),
                **extra_kwargs,
                **{k: v for k, v in kwargs.items() if k not in ["model", "max_tokens", "tools"]},
            ) as stream:
                # Register the abort listener BEFORE the iterator pulls
                # its first chunk, so a signal that fires between context
                # entry and the first ``text_stream.__next__`` still wins
                # the race. Mirrors ``StreamWatchdog``'s close pattern:
                # close the underlying HTTP response from another thread,
                # which raises in the consumer thread on the next pull.
                if abort_signal is not None:
                    def _close_stream_on_abort() -> None:
                        try:
                            response = getattr(stream, "response", None)
                            if response is not None:
                                close = getattr(response, "close", None)
                                if callable(close):
                                    close()
                        except Exception:
                            # Best-effort — never let the close
                            # propagate out of the listener thread.
                            pass

                    # Register-then-recheck (NOT check-then-register):
                    # the naive ordering has a sub-microsecond race
                    # where another thread can call ``_fire`` between
                    # our ``aborted`` read and the ``add_listener``
                    # append. ``_fire`` snapshots the listener list
                    # before iterating, so any listener appended after
                    # that snapshot is silently dropped.
                    # Register-then-recheck closes the gap: ``aborted``
                    # is sticky-True after ``_fire`` runs, so the
                    # post-add read catches any concurrent fire, and
                    # ``_close_stream_on_abort`` is idempotent so a
                    # double-call (listener fires AND we call directly)
                    # is harmless.
                    abort_listener = abort_signal.add_listener(
                        _close_stream_on_abort, once=True,
                    )
                    if abort_signal.aborted:
                        _close_stream_on_abort()

                watchdog = StreamWatchdog(stream)
                watchdog.arm()
                try:
                    for text in stream.text_stream:
                        # Each chunk pushes the deadline forward.
                        watchdog.reset()
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
            # Abort path: the abort listener closed the stream's response,
            # which raised in the consumer thread. Translate to
            # ``AbortError`` so the query loop's
            # ``except AbortError: raise`` cancel boundary unwinds
            # cleanly. We check the signal AFTER the catch (not the
            # exception type) because the SDK can raise several different
            # exception classes depending on which socket operation was
            # in flight when we closed; the abort_signal state is the
            # authoritative source of truth.
            if abort_signal is not None and abort_signal.aborted:
                raise AbortError(abort_signal.reason or "user_interrupt") from streaming_exc

            # WI-5.2 fallback path: stream interrupted by the idle
            # watchdog. Fall back to non-streaming so the user still
            # gets an answer. If the failure is something else
            # (network/auth/etc.), re-raise the original.
            if watchdog_fired:
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
        finally:
            # Always detach the abort listener so it doesn't pin the
            # provider alive past one call.
            if abort_listener is not None and abort_signal is not None:
                abort_signal.remove_listener(abort_listener)

        # Stream completed normally but abort may have fired between
        # ``stream.__exit__`` and here. Surface it now so the caller
        # bails at the same place every other path does.
        if abort_signal is not None and abort_signal.aborted:
            raise AbortError(abort_signal.reason or "user_interrupt")

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
