"""OpenAI provider implementation.

Two request paths:

- **API key** (default): OpenAI SDK against ``/v1/chat/completions`` via
  :class:`OpenAICompatibleProvider` — unchanged behaviour.
- **ChatGPT subscription**: when no API key is configured but the user has
  connected a ChatGPT Plus/Pro plan (``clawcodex login`` →
  ``src/auth/openai_subscription.py``), requests go to the ChatGPT Codex
  backend (``https://chatgpt.com/backend-api/codex/responses``) speaking the
  Responses API — the mechanism OpenCode's ``openai`` plugin uses
  (reference_projects/opencode/packages/opencode/src/plugin/openai/codex.ts).
  Wire-format conversion lives in ``src/providers/openai_responses.py``.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from typing import Any, Generator, Optional
from urllib.parse import urlparse
from uuid import uuid4

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from .base import BaseProvider, ChatResponse, MessageInput, TextChunkCallback
from .openai_compatible import (
    _CHUNK_QUEUE_MAXSIZE,
    OpenAICompatibleProvider,
    _parse_tool_call_arguments,
)
from .openai_responses import (
    RESPONSES_ITEM_BLOCK_TYPE,
    INCLUDE_ENCRYPTED_REASONING,
    SUBSCRIPTION_MODELS,
    build_usage_dict,
    convert_messages_to_responses_input,
    convert_tools_to_responses_format,
    parse_sse_line,
    strip_item_for_replay,
    supports_verbosity,
)

logger = logging.getLogger(__name__)

_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def _subscription_reasoning_effort() -> str:
    """Reasoning effort for subscription requests.

    ``medium`` is both OpenCode's default (transform.ts:1176) and the
    backend's own default_reasoning_level (Codex CLI models cache).
    Overridable via ``CLAWCODEX_OPENAI_REASONING_EFFORT``.
    """
    effort = os.environ.get("CLAWCODEX_OPENAI_REASONING_EFFORT", "").strip().lower()
    return effort if effort in _REASONING_EFFORTS else "medium"


class _HttpxStreamHolder:
    """Adapter so ``StreamAbortGuard.attach`` can close an httpx response.

    The guard closes ``stream.response`` on abort — the SDK stream objects
    expose that attribute; for the raw-httpx subscription path this shim
    provides it.
    """

    __slots__ = ("response",)

    def __init__(self, response: Any) -> None:
        self.response = response


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI provider using OpenAI SDK (API key) or the ChatGPT Codex
    backend (subscription OAuth)."""

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        """Initialize OpenAI provider.

        Args:
            api_key: OpenAI API key
            base_url: Base URL (optional, for custom endpoints)
            model: Default model (default: gpt-5.4)
        """
        super().__init__(api_key, base_url, model or "gpt-5.4")

        self._subscription_active = False
        self._subscription_account_id = ""
        # One id per provider instance ≈ one per session: rides the
        # ``session_id`` header and ``prompt_cache_key`` so the backend can
        # route consecutive requests to the same prompt cache.
        self._subscription_session_id = str(uuid4())
        # A configured API key deliberately wins. With no key, fall back to
        # the user's explicitly stored ChatGPT OAuth login — but only against
        # the first-party endpoint (custom base URLs mean a proxy/gateway
        # that expects the configured key semantics). Same policy as the
        # Anthropic provider's Claude-subscription fallback.
        oauth_eligible = not base_url or urlparse(base_url).hostname == "api.openai.com"
        if not api_key and oauth_eligible:
            from src.auth.openai_subscription import get_valid_credentials

            try:
                credentials = get_valid_credentials()
            except RuntimeError as exc:
                # A broken refresh must not brick provider construction —
                # surface at request time instead (validation already passed
                # on the credential file's existence).
                logger.warning("ChatGPT subscription refresh failed: %s", exc)
                credentials = None
                from src.auth.openai_subscription import load_credentials

                if load_credentials() is not None:
                    self._subscription_active = True
            if credentials is not None:
                self._subscription_active = True
                self._subscription_account_id = credentials.account_id

    def _create_client(self) -> Any:
        """Create OpenAI SDK client (API-key path only).

        The read timeout that prevents a stalled stream from freezing the event
        loop is applied centrally by ``OpenAICompatibleProvider.client`` (via
        ``_apply_client_timeout``) for every provider, so it isn't set here.
        """
        if OpenAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "openai package is not installed. Install optional dependencies to use OpenAIProvider."
            )
        kwargs: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        # Support SSL verification bypass for corporate/internal endpoints.
        if os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
            import httpx
            kwargs["http_client"] = httpx.Client(verify=False)
        return OpenAI(**kwargs)

    def get_available_models(self) -> list[str]:
        """Get list of available OpenAI models.

        Returns:
            List of model names
        """
        if self._subscription_active:
            return list(SUBSCRIPTION_MODELS)
        return [
            # GPT-5.5 (flagship; also the ChatGPT-subscription default family)
            "gpt-5.5",
            # GPT-5.4 series
            "gpt-5.4",
            "gpt-5.4-pro",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            # GPT-5.2 series
            "gpt-5.2",
            "gpt-5.2-pro",
            "gpt-5.2-mini",
            "gpt-5.2-nano",
            # Codex (coding-specialized)
            "gpt-5.3-codex",
            "gpt-5.3-codex-spark",
            # Legacy GPT-4 series
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4-turbo",
            "gpt-4",
            "gpt-3.5-turbo",
        ]

    # ------------------------------------------------------------------
    # ChatGPT-subscription path (Responses API against the Codex backend)
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> ChatResponse:
        if self._subscription_active:
            return self._subscription_stream_request(messages, tools, **kwargs)
        return super().chat(messages, tools, **kwargs)

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        if not self._subscription_active:
            yield from super().chat_stream(messages, tools, **kwargs)
            return
        # Callback → generator adaptation: run the request on a worker and
        # relay text deltas through a bounded queue.
        chunk_queue: queue.Queue = queue.Queue(maxsize=_CHUNK_QUEUE_MAXSIZE)
        _DONE = object()

        def _run() -> None:
            try:
                self._subscription_stream_request(
                    messages, tools,
                    on_text_chunk=lambda piece: chunk_queue.put(piece),
                    **kwargs,
                )
            except BaseException as exc:  # noqa: BLE001 — surface to consumer
                chunk_queue.put(exc)
            finally:
                chunk_queue.put(_DONE)

        worker = threading.Thread(target=_run, daemon=True, name="openai-subscription-stream")
        worker.start()
        while True:
            item = chunk_queue.get()
            if item is _DONE:
                return
            if isinstance(item, BaseException):
                raise item
            yield item

    def chat_stream_response(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        on_text_chunk: TextChunkCallback | None = None,
        abort_signal: Any = None,
        on_thinking_chunk: TextChunkCallback | None = None,
        **kwargs,
    ) -> ChatResponse:
        if self._subscription_active:
            return self._subscription_stream_request(
                messages,
                tools,
                on_text_chunk=on_text_chunk,
                on_thinking_chunk=on_thinking_chunk,
                abort_signal=abort_signal,
                **kwargs,
            )
        return super().chat_stream_response(
            messages,
            tools,
            on_text_chunk=on_text_chunk,
            abort_signal=abort_signal,
            on_thinking_chunk=on_thinking_chunk,
            **kwargs,
        )

    def _subscription_request_body(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]],
        **kwargs,
    ) -> dict[str, Any]:
        model = self._get_model(**kwargs)
        # RAW Anthropic-shape messages (dict conversion + image validation
        # only) — deliberately NOT the base class's Chat Completions
        # conversion; the Responses converter owns the translation.
        prepared = BaseProvider._prepare_messages(self, messages)
        input_items, instructions = convert_messages_to_responses_input(prepared)
        # Side paths (compaction, agent hooks, memdir selector) pass an
        # Anthropic-style ``system`` kwarg instead of a system message.
        system_kwarg = kwargs.get("system")
        if system_kwarg:
            from .openai_responses import _system_text

            system_text = _system_text(system_kwarg)
            if system_text:
                instructions = (
                    f"{system_text}\n\n{instructions}" if instructions else system_text
                )

        body: dict[str, Any] = {
            "model": model,
            "input": input_items,
            # Stateless mode: the backend stores nothing; encrypted reasoning
            # comes back inline and is replayed by the converter next turn.
            "store": False,
            "stream": True,
            "include": list(INCLUDE_ENCRYPTED_REASONING),
            "reasoning": {
                "effort": _subscription_reasoning_effort(),
                "summary": "auto",
            },
            "prompt_cache_key": self._subscription_session_id,
        }
        if instructions:
            body["instructions"] = instructions
        if tools:
            converted = convert_tools_to_responses_format(tools)
            if converted:
                body["tools"] = converted
        if supports_verbosity(model):
            # OpenCode sends verbosity=low for gpt-5.x non-codex non-chat
            # (transform.ts:1189); matches the backend's own default.
            body["text"] = {"verbosity": "low"}
        # NOTE: remaining kwargs (max_tokens, temperature, …) are
        # intentionally NOT forwarded. The Codex backend rejects sampler
        # params on reasoning models, and OpenCode explicitly forces
        # maxOutputTokens off for this provider ("Match codex cli",
        # plugin/openai/codex.ts:637-641).
        return body

    def _subscription_headers(self, access_token: str) -> dict[str, str]:
        from src.auth.openai_subscription import ORIGINATOR

        headers = {
            "Authorization": f"Bearer {access_token}",
            "OpenAI-Beta": "responses=experimental",
            "originator": ORIGINATOR,
            "session_id": self._subscription_session_id,
            "Accept": "text/event-stream",
        }
        if self._subscription_account_id:
            headers["chatgpt-account-id"] = self._subscription_account_id
        return headers

    def _subscription_stream_request(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        on_text_chunk: TextChunkCallback | None = None,
        on_thinking_chunk: TextChunkCallback | None = None,
        abort_signal: Any = None,
        **kwargs,
    ) -> ChatResponse:
        """POST to the Codex backend and rebuild a ChatResponse from SSE.

        Same ESC-abort architecture as
        ``OpenAICompatibleProvider.chat_stream_response``: the blocking
        socket reads run on a daemon worker pushing lines into a bounded
        queue; the main thread polls with a 100 ms tick and re-checks the
        abort signal between ticks, so the user's prompt returns promptly
        regardless of socket state. See that method's docstring for the
        full rationale.
        """
        import httpx

        from src.auth.openai_subscription import (
            CODEX_API_ENDPOINT,
            force_refresh,
            get_valid_credentials,
        )
        from ._stream_abort import StreamAbortGuard

        guard = StreamAbortGuard(abort_signal)
        guard.raise_if_pre_aborted()

        credentials = get_valid_credentials()
        if credentials is None:
            raise RuntimeError(
                "ChatGPT subscription login was removed; run `clawcodex login`"
            )
        self._subscription_account_id = (
            credentials.account_id or self._subscription_account_id
        )

        body = self._subscription_request_body(messages, tools, **kwargs)

        read = float(os.environ.get("CLAWCODEX_LLM_READ_TIMEOUT", "120"))
        connect = float(os.environ.get("CLAWCODEX_LLM_CONNECT_TIMEOUT", "15"))
        timeout = httpx.Timeout(connect=connect, read=read, write=30.0, pool=15.0)
        verify = os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() not in (
            "0", "false", "no",
        )

        client = httpx.Client(timeout=timeout, verify=verify)
        response: Any = None
        try:
            response = client.send(
                client.build_request(
                    "POST",
                    CODEX_API_ENDPOINT,
                    headers=self._subscription_headers(credentials.access_token),
                    json=body,
                ),
                stream=True,
            )
            if response.status_code == 401:
                # Server-side invalidation ahead of local expiry — refresh
                # once and retry.
                response.close()
                refreshed = force_refresh()
                if refreshed is None:
                    raise RuntimeError(
                        "ChatGPT subscription login expired; run `clawcodex login`"
                    )
                self._subscription_account_id = (
                    refreshed.account_id or self._subscription_account_id
                )
                response = client.send(
                    client.build_request(
                        "POST",
                        CODEX_API_ENDPOINT,
                        headers=self._subscription_headers(refreshed.access_token),
                        json=body,
                    ),
                    stream=True,
                )
            if response.status_code != 200:
                detail = response.read().decode("utf-8", "replace")
                raise RuntimeError(
                    f"ChatGPT backend error ({response.status_code}): {detail[:600]}"
                )
            return self._consume_subscription_stream(
                response, guard, on_text_chunk, on_thinking_chunk,
                request_model=str(body.get("model", "")),
            )
        except Exception as exc:
            guard.reraise_if_aborted(exc)
            raise
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            client.close()

    def _consume_subscription_stream(
        self,
        response: Any,
        guard: Any,
        on_text_chunk: TextChunkCallback | None,
        on_thinking_chunk: TextChunkCallback | None,
        request_model: str,
    ) -> ChatResponse:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        items: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []
        usage: dict[str, Any] = {"billing_mode": "subscription"}
        response_model = request_model
        finish_reason = "stop"
        failure: str | None = None

        _DONE = object()
        line_queue: queue.Queue = queue.Queue(maxsize=_CHUNK_QUEUE_MAXSIZE)

        def _drain() -> None:
            try:
                for line in response.iter_lines():
                    line_queue.put(line)
            except BaseException as exc:  # noqa: BLE001 — surface to consumer
                line_queue.put(exc)
            finally:
                line_queue.put(_DONE)

        worker = threading.Thread(
            target=_drain, daemon=True, name=f"openai-subscription-{id(response)}"
        )

        with guard.attach(_HttpxStreamHolder(response)):
            worker.start()
            while True:
                try:
                    item = line_queue.get(timeout=0.1)
                except queue.Empty:
                    if guard.aborted:
                        guard.raise_if_post_aborted()
                    continue
                if item is _DONE:
                    break
                if isinstance(item, BaseException):
                    if isinstance(item, Exception):
                        guard.reraise_if_aborted(item)
                        raise item
                    raise item

                event = parse_sse_line(str(item))
                if event is None:
                    continue
                etype = event.get("type", "")

                if etype == "response.output_text.delta":
                    delta = str(event.get("delta", "") or "")
                    if delta:
                        content_parts.append(delta)
                        if on_text_chunk is not None:
                            on_text_chunk(delta)
                elif etype == "response.reasoning_summary_text.delta":
                    delta = str(event.get("delta", "") or "")
                    if delta:
                        reasoning_parts.append(delta)
                        if on_thinking_chunk is not None:
                            on_thinking_chunk(delta)
                elif etype == "response.output_item.done":
                    raw_item = event.get("item")
                    if isinstance(raw_item, dict):
                        stripped = strip_item_for_replay(raw_item)
                        items.append(stripped)
                        if stripped.get("type") == "function_call":
                            tool_uses.append({
                                "id": str(stripped.get("call_id", "")),
                                "name": str(stripped.get("name", "")),
                                "input": _parse_tool_call_arguments(
                                    stripped.get("arguments")
                                ),
                            })
                elif etype == "response.completed":
                    payload = event.get("response") or {}
                    usage = build_usage_dict(payload.get("usage"))
                    response_model = str(payload.get("model") or response_model)
                elif etype == "response.incomplete":
                    payload = event.get("response") or {}
                    details = payload.get("incomplete_details") or {}
                    if "max_output_tokens" in str(details.get("reason", "")):
                        finish_reason = "max_tokens"
                    usage = build_usage_dict(payload.get("usage"))
                elif etype in ("response.failed", "error"):
                    if etype == "error":
                        failure = str(event.get("message") or event)
                    else:
                        error = (event.get("response") or {}).get("error") or {}
                        failure = str(error.get("message") or error or event)

                if guard.aborted:
                    guard.raise_if_post_aborted()

        guard.raise_if_post_aborted()
        if failure:
            raise RuntimeError(f"ChatGPT backend request failed: {failure}")

        if tool_uses and finish_reason == "stop":
            finish_reason = "tool_calls"
        raw_blocks = [
            {"type": RESPONSES_ITEM_BLOCK_TYPE, "item": item} for item in items
        ]
        return ChatResponse(
            content="".join(content_parts),
            model=response_model,
            usage=usage,
            finish_reason=finish_reason,
            reasoning_content="".join(reasoning_parts) or None,
            tool_uses=tool_uses or None,
            raw_content_blocks=raw_blocks or None,
        )
