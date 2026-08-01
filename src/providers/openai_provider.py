"""OpenAI provider implementation.

PROTOCOL and ROUTE are separate axes here. The protocol follows the MODEL,
the route follows the AUTH — conflating them is what this module got wrong
for a while, and the split is the thing to preserve when editing it.

Protocol (``_use_responses``):

- **Responses API** for reasoning models (gpt-5.x, o-series, codex).
  ``/v1/chat/completions`` supports them inconsistently — it rejects tools
  outright for some, tools-plus-effort for others — and an agentic run
  always sends tools. Responses serves them all uniformly.
- **Chat Completions** via the OpenAI SDK and
  :class:`OpenAICompatibleProvider` for everything else (gpt-4o, ``-chat``
  variants, …), which is the older and more heavily exercised path.

Route (``_subscription_stream_request``), for the Responses protocol only:

- **API key**: ``{base_url}/responses`` with a bearer key. Metered, so its
  usage is billed and its effort ceiling is ``xhigh``.
- **ChatGPT subscription**: no API key configured but a connected Plus/Pro
  plan (``clawcodex login`` → ``src/auth/openai_subscription.py``) sends to
  the Codex backend (``https://chatgpt.com/backend-api/codex/responses``)
  with OAuth — the mechanism OpenCode's ``openai`` plugin uses
  (reference_projects/opencode/packages/opencode/src/plugin/openai/codex.ts).
  Flat-rate, so its usage is not billed, its effort tops out at ``high``,
  and it rejects sampler params the public API accepts.

Anything keyed on "is this a subscription" must therefore be about the
ROUTE, never the protocol: billing, the effort ceiling, ``max_output_tokens``
and the 401-refresh dance all differ, and the two share every other byte.
A non-``api.openai.com`` base URL — from config OR ``$OPENAI_BASE_URL`` —
means a proxy, which disables both the Responses switch and the OAuth
fallback. Wire-format conversion lives in ``src/providers/openai_responses.py``.
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
    normalize_openai_effort,
    supports_reasoning,
    build_usage_dict,
    convert_messages_to_responses_input,
    convert_tools_to_responses_format,
    parse_sse_line,
    strip_item_for_replay,
    supports_verbosity,
)

logger = logging.getLogger(__name__)

_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def _subscription_reasoning_effort(requested: str | None = None) -> str:
    """Reasoning effort for subscription requests.

    Precedence: the session's ``/effort`` setting (arrives as
    ``extra_body.reasoning_effort``, injected at the wire boundary by
    ``query.py::_call_model_sync`` for every OpenAI-compatible provider)
    → ``CLAWCODEX_OPENAI_REASONING_EFFORT`` → ``medium`` (OpenCode's
    default, transform.ts:1176, and the backend's own
    default_reasoning_level).

    ``xhigh``/``max`` clamp to ``high`` HERE, and only here: this is the
    ChatGPT-subscription backend (chatgpt.com/backend-api/codex), whose
    general gpt-5.x models advertise low/medium/high and reject higher
    tiers (probed 2026-07-25). That is narrower than the public API —
    developers.openai.com/api/docs/guides/reasoning lists none | minimal |
    low | medium | high | xhigh | max and notes support varies by model —
    and narrower than what a gateway may accept (``openai/gpt-5.6-luna``
    via OpenRouter takes both ``xhigh`` and ``max``, probed 2026-07-31,
    with reasoning-token counts rising monotonically across the ladder).
    So the clamp is a property of THIS backend, not of the level names;
    the generic OpenAI-compatible path deliberately does not clamp.
    """
    for candidate in (requested, os.environ.get("CLAWCODEX_OPENAI_REASONING_EFFORT")):
        effort = (candidate or "").strip().lower()
        if effort in ("xhigh", "max"):
            return "high"
        if effort in _REASONING_EFFORTS:
            return effort
    return "medium"


class _HttpxStreamHolder:
    """Adapter so ``StreamAbortGuard.attach`` can close an httpx response.

    The guard closes ``stream.response`` on abort — the SDK stream objects
    expose that attribute; for the raw-httpx subscription path this shim
    provides it.
    """

    __slots__ = ("response",)

    def __init__(self, response: Any) -> None:
        self.response = response


class ResponsesHTTPError(RuntimeError):
    """A non-200 from the Responses endpoint, carrying its status.

    The retry layer classifies purely by attribute: ``query.py`` reads
    ``e.status_code`` to decide whether an error is retryable, and
    ``_retry_after_seconds`` reads ``e.response.headers`` to honour
    ``Retry-After``. A bare ``RuntimeError`` has neither, so a 429 or a 503
    here looked like a permanent failure — the request was abandoned instead
    of backed off, and the server's own pacing hint was discarded.

    That was survivable while this path served only ChatGPT subscriptions,
    which rate-limit by plan. It is not survivable for API keys, where 429
    is routine under the concurrency an eval run generates.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` handlers
    keep catching it.
    """

    def __init__(self, message: str, *, status_code: int, response: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI provider: Responses API for reasoning models, Chat Completions
    via the OpenAI SDK for the rest, over either an API key or ChatGPT
    subscription OAuth. See the module docstring for the protocol/route
    split."""

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
        #
        # Shares ``_is_first_party_base_url`` rather than re-deriving the
        # rule, so the ``$OPENAI_BASE_URL`` channel cannot be honoured in one
        # place and ignored in the other. It reached this branch first: an
        # OAuth session cannot be proxied, so with a proxy in the env, no key
        # and a stale ChatGPT login, prompts went to chatgpt.com with no
        # error — the exact outcome this guard exists to prevent.
        # ``super().__init__`` above has already populated ``self.base_url``.
        oauth_eligible = self._is_first_party_base_url()
        if not api_key and oauth_eligible:
            # Presence check only — deliberately NOT ``get_valid_credentials``:
            # that can perform a blocking token refresh (30 s urllib timeout),
            # and providers are constructed at startup and on every /model
            # switch. The request path refreshes right before each call, so
            # freshness at construction time buys nothing.
            from src.auth.openai_subscription import load_credentials

            credentials = load_credentials()
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
            # A ChatGPT plan serves a SMALLER set than the API key does, so
            # this stays its own list — see openai_responses.SUBSCRIPTION_MODELS.
            return list(SUBSCRIPTION_MODELS)
        # Read the registry rather than keep a second copy. This used to
        # duplicate PROVIDER_INFO["openai"]["available_models"] verbatim, and
        # the halves feed different surfaces — the registry drives `login` and
        # the /model picker, this drives discovery — so an update to one shows
        # up as a model one offers and the other drops.
        from . import PROVIDER_INFO

        return list(PROVIDER_INFO["openai"]["available_models"])

    # ------------------------------------------------------------------
    # Responses-API path (API key against /v1/responses, or the ChatGPT
    # subscription against the Codex backend — same protocol, two routes)
    # ------------------------------------------------------------------

    def _use_responses(self, **kwargs: Any) -> bool:
        """Whether this request goes over the Responses protocol.

        Keyed on the MODEL, never on the auth mode — the shape OpenCode's
        provider facade takes (``model: responses``, with ``chat`` an
        explicit opt-in).

        The reason is that Chat Completions supports reasoning models
        INCONSISTENTLY, per model. Probed live against the real API
        2026-08-01, all with tools attached:

            model          tools, no effort   tools + effort
            gpt-5          200                200
            gpt-5.4        200                400
            gpt-5.6-luna   400                400

        with the 400 reading "Function tools with reasoning_effort are not
        supported for <model> in /v1/chat/completions. To use function tools,
        use /v1/responses or set reasoning_effort to 'none'." — it fires for
        luna even with NO effort in the body, because that model's default
        reasoning level is not 'none'.

        So the endpoint's tool support is a per-model minefield that shifts
        with each release, and an agentic run always sends tools. Responses
        serves all of them uniformly and is the endpoint the error itself
        points at, so routing every reasoning model there replaces the
        minefield with one rule.

        Note this DOES move models that work today (gpt-5, and gpt-5.4 when
        no effort is set) onto the Responses path. That is the intended
        trade: one uniformly-supported protocol over a per-model matrix.

        Non-reasoning models (gpt-4o, gpt-3.5-turbo) stay on Chat
        Completions. Responses serves them too, but they have no reasoning
        block to negotiate and therefore no defect to fix, and that path
        carries the older, more heavily exercised streaming code.
        """
        if self._subscription_active:
            return True
        if not self.api_key:
            return False
        if not self._is_first_party_base_url():
            return False
        return supports_reasoning(self._get_model(**kwargs))

    def _is_first_party_base_url(self) -> bool:
        """Whether requests go to OpenAI itself rather than a gateway.

        ``providers.openai.base_url`` is user-configurable (config.py), so
        this provider is also how people reach LiteLLM/vLLM/Azure-style
        proxies. Those speak Chat Completions universally but implement
        ``/responses`` only sometimes, so switching protocol underneath one
        would turn a working setup into a 404.

        The 400s that motivate the Responses route are api.openai.com's own
        behaviour, and cannot be assumed to apply to a proxy that normalises
        requests. So the switch is scoped to the host whose behaviour was
        actually measured; everything else keeps the protocol it has today.
        """
        base = self._configured_base_url()
        if not base:
            return True
        return (urlparse(base).hostname or "").lower() == "api.openai.com"

    def _configured_base_url(self) -> str:
        """The base URL actually in force, from EITHER channel.

        ``self.base_url`` is commonly None — ``set_api_key`` only writes the
        key when one is passed (config.py), and the server forwards
        ``provider_cfg.get("base_url")`` — in which case the OpenAI SDK falls
        back to ``$OPENAI_BASE_URL`` (openai/_client.py). Reading only the
        attribute would therefore see "first-party" for a session that the
        Chat Completions path sends to a proxy, and the two protocols would
        diverge to different hosts: an egress proxy configured by env var
        would be bypassed for the default model, carrying the key and the
        conversation straight to OpenAI with no error. ``$OPENAI_BASE_URL``
        is a documented knob here (eval/README.md).
        """
        return (self.base_url or os.environ.get("OPENAI_BASE_URL") or "").strip()

    def _without_unsupported_reasoning(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Drop ``reasoning_effort`` for models that have no reasoning.

        The wire boundary injects ``extra_body.reasoning_effort`` for every
        OpenAI-compatible provider, which is right for the reasoning models
        but a hard 400 on the rest::

            gpt-4o + reasoning_effort=high
              -> 400 "Unrecognized request argument supplied: reasoning_effort"

        so ``--provider openai --model gpt-4o --effort high`` could not make a
        single call. Gating on the model mirrors how OpenCode attaches
        reasoning options only to models that declare the capability.

        Scoped to the first-party provider on purpose: the capability check
        keys on OpenAI's own naming, and other OpenAI-compatible providers
        (OpenRouter, DeepSeek) namespace their ids differently and accept the
        field on models this predicate would not recognise. Applying it
        globally would silently drop effort for them — including the
        ``openrouter/openai/gpt-5.6-luna`` configuration the evals run on.
        """
        extra = kwargs.get("extra_body") or {}
        if "reasoning_effort" not in extra:
            return kwargs
        if supports_reasoning(self._get_model(**kwargs)):
            return kwargs
        pruned = {k: v for k, v in extra.items() if k != "reasoning_effort"}
        out = dict(kwargs)
        # Preserve an explicitly-empty extra_body rather than dropping the key,
        # so callers that inspect it see the same shape they passed.
        out["extra_body"] = pruned
        return out

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> ChatResponse:
        if self._use_responses(**kwargs):
            return self._subscription_stream_request(messages, tools, **kwargs)
        return super().chat(
            messages, tools, **self._without_unsupported_reasoning(kwargs)
        )

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs,
    ) -> Generator[str, None, None]:
        if not self._use_responses(**kwargs):
            yield from super().chat_stream(
                messages, tools, **self._without_unsupported_reasoning(kwargs)
            )
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

    def _stream_attempt(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        on_text_chunk: TextChunkCallback | None = None,
        abort_signal: Any = None,
        on_thinking_chunk: TextChunkCallback | None = None,
        **kwargs,
    ) -> ChatResponse:
        """One streaming attempt, over whichever protocol this model uses.

        Overriding the ATTEMPT rather than ``chat_stream_response`` is what
        keeps the Responses path inside the base class's transport-drop retry
        loop. That retry is load-bearing rather than theoretical: per
        ``OpenAICompatibleProvider.chat_stream_response``, an un-retried
        ``peer closed connection`` ended 8 of 89 terminal-bench 2.1 trials.
        Returning early from ``chat_stream_response`` — as this class did
        while the protocol was keyed on auth — silently opts every Responses
        request out of it.
        """
        if self._use_responses(**kwargs):
            return self._subscription_stream_request(
                messages,
                tools,
                on_text_chunk=on_text_chunk,
                abort_signal=abort_signal,
                on_thinking_chunk=on_thinking_chunk,
                **kwargs,
            )
        return super()._stream_attempt(
            messages,
            tools,
            on_text_chunk=on_text_chunk,
            abort_signal=abort_signal,
            on_thinking_chunk=on_thinking_chunk,
            **self._without_unsupported_reasoning(kwargs),
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
            "prompt_cache_key": self._subscription_session_id,
        }
        # ``reasoning`` is gated on the MODEL, not the auth mode. Sending it
        # to a non-reasoning model is a hard 400 ("Unsupported parameter:
        # 'reasoning.effort' is not supported with this model" — gpt-4o,
        # verified 2026-08-01), and the identical request without the block
        # succeeds. That gate is what lets this protocol serve every OpenAI
        # model rather than only the reasoning ones.
        if supports_reasoning(model):
            if self._subscription_active:
                # The ChatGPT backend advertises only low/medium/high and
                # rejects higher tiers, so it keeps its own clamp.
                effort = _subscription_reasoning_effort(
                    (kwargs.get("extra_body") or {}).get("reasoning_effort")
                )
            else:
                # The public API accepts xhigh; only ``max`` is unsupported,
                # and it degrades rather than failing the request.
                effort = normalize_openai_effort(
                    (kwargs.get("extra_body") or {}).get("reasoning_effort")
                )
            if effort:
                body["reasoning"] = {"effort": effort, "summary": "auto"}
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
        # Remaining sampler kwargs (temperature, top_p, …) are intentionally
        # NOT forwarded: the Codex backend rejects them on reasoning models,
        # and OpenCode forces maxOutputTokens off for it ("Match codex cli",
        # plugin/openai/codex.ts:637-641).
        #
        # That rationale is about the CODEX BACKEND, though, so it stops
        # applying once an API key uses this same protocol. The public API
        # accepts ``max_output_tokens``, and callers rely on it as a bound
        # rather than a preference: compaction summaries
        # (COMPACT_MAX_OUTPUT_TOKENS), the permission classifier (512), the
        # /goal judge, and the advisor all pass one. Dropping it silently
        # unbounds them. Worse, query.py's ``max_output_tokens_escalate``
        # lane re-issues with ESCALATED_MAX_TOKENS after a truncated reply —
        # if the value never reaches the wire, that retry is byte-identical
        # to the request that just truncated, so it burns a full-context
        # turn to reproduce the same failure.
        if not self._subscription_active:
            max_tokens = kwargs.get("max_tokens")
            if max_tokens:
                body["max_output_tokens"] = int(max_tokens)
        return body

    def _responses_endpoint(self) -> str:
        """The Responses URL for the API-key route.

        Derived from ``base_url`` so a proxy/gateway configuration keeps
        working; falls back to the first-party endpoint. Kept separate from
        the subscription's ``CODEX_API_ENDPOINT`` because they are two ROUTES
        to the same protocol, not two protocols.
        """
        base = (self._configured_base_url() or "https://api.openai.com/v1").rstrip("/")
        return f"{base}/responses"

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

        # ROUTE = endpoint + headers. The wire FORMAT below is identical for
        # both; only where it is sent and how it authenticates differ. That
        # split is the point of this method: protocol is a property of the
        # provider, auth is a separate axis (OpenCode models it the same way —
        # its Codex plugin rewrites the URL and swaps the bearer token, and
        # the endpoint it rewrites TO is still ``/responses``).
        credentials = None
        if self._subscription_active:
            credentials = get_valid_credentials()
            if credentials is None:
                raise RuntimeError(
                    "ChatGPT subscription login was removed; run `clawcodex login`"
                )
            self._subscription_account_id = (
                credentials.account_id or self._subscription_account_id
            )
            endpoint = CODEX_API_ENDPOINT
            headers = self._subscription_headers(credentials.access_token)
        else:
            endpoint = self._responses_endpoint()
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            }

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
                    "POST", endpoint, headers=headers, json=body,
                ),
                stream=True,
            )
            if response.status_code == 401 and self._subscription_active:
                # Server-side invalidation ahead of local expiry — refresh
                # once and retry. SUBSCRIPTION ONLY: on the API-key route a
                # 401 means the key is rejected, and there is no OAuth token
                # to refresh — running the refresh dance there would raise a
                # "login expired" error for what is really a bad key, hiding
                # the real cause. It falls through to the status check below,
                # which surfaces the provider's own message.
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
                _who = "ChatGPT backend" if self._subscription_active else "OpenAI API"
                raise ResponsesHTTPError(
                    f"{_who} error ({response.status_code}): {detail[:600]}",
                    status_code=response.status_code,
                    response=response,
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
        # An API key is metered per token, a ChatGPT plan is flat-rate, and
        # this stream now serves both — so the flag that zeroes cost in
        # cost_tracker must follow the auth mode rather than the protocol.
        subscription = self._subscription_active
        usage: dict[str, Any] = (
            {"billing_mode": "subscription"} if subscription else {}
        )
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
                    usage = build_usage_dict(
                        payload.get("usage"), subscription=subscription
                    )
                    response_model = str(payload.get("model") or response_model)
                elif etype == "response.incomplete":
                    payload = event.get("response") or {}
                    details = payload.get("incomplete_details") or {}
                    if "max_output_tokens" in str(details.get("reason", "")):
                        finish_reason = "max_tokens"
                    usage = build_usage_dict(
                        payload.get("usage"), subscription=subscription
                    )
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
