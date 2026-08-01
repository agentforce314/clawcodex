"""Protocol/route selection for the first-party OpenAI provider.

The provider used to pick its protocol from the AUTH MODE: subscription meant
Responses, an API key meant Chat Completions. That conflated two independent
axes and made the API-key route unusable for agentic work, because

    /v1/chat/completions + tools + gpt-5.6-luna -> 400
      "Function tools with reasoning_effort are not supported ... use
       /v1/responses or set reasoning_effort to 'none'."

fires even with no effort set. These tests pin the corrected split: the
PROTOCOL follows the model's capability, while auth only decides the ROUTE
(endpoint + headers) and the effort ceiling.
"""

from __future__ import annotations

import time
from unittest.mock import patch

from src.auth import openai_subscription as auth
from src.auth.openai_subscription import CODEX_API_ENDPOINT
from src.providers.openai_provider import OpenAIProvider
from src.providers.openai_responses import (
    normalize_openai_effort,
    supports_reasoning,
)

REASONING = ["gpt-5.6-luna", "gpt-5.5", "gpt-5", "o1-preview", "o3-mini", "o4-mini",
             "gpt-5-codex", "codex-mini-latest"]
PLAIN = ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-3.5-turbo", "chatgpt-4o-latest",
         # the non-reasoning variants of a reasoning family — the prefix
         # alone would misclassify these
         "gpt-5-chat-latest", "gpt-5-chat"]


def _credentials() -> auth.SubscriptionCredentials:
    return auth.SubscriptionCredentials(
        "access", "refresh", time.time() + 3600, "acct-123", "idtok"
    )


# --- capability predicate -----------------------------------------------


def test_supports_reasoning_splits_the_model_families() -> None:
    for model in REASONING:
        assert supports_reasoning(model) is True, model
    for model in PLAIN:
        assert supports_reasoning(model) is False, model


def test_supports_reasoning_tolerates_missing_and_odd_case() -> None:
    assert supports_reasoning("") is False
    assert supports_reasoning(None) is False  # type: ignore[arg-type]
    assert supports_reasoning("GPT-5.6-Luna") is True


# --- effort normalisation -----------------------------------------------


def test_max_is_clamped_to_xhigh_for_openai() -> None:
    """OpenAI's ladder tops out at xhigh; `max` is an Anthropic-only rung.

    Clamping rather than rejecting keeps `--effort max` portable across
    providers, which is how the evals drive it.
    """
    assert normalize_openai_effort("max") == "xhigh"
    assert normalize_openai_effort("MAX") == "xhigh"


def test_known_efforts_pass_through_and_junk_is_dropped() -> None:
    for effort in ("none", "low", "medium", "high", "xhigh"):
        assert normalize_openai_effort(effort) == effort
    for junk in ("bogus", "", "  ", None):
        assert normalize_openai_effort(junk) is None


# --- protocol selection --------------------------------------------------


def test_api_key_route_uses_responses_for_reasoning_models() -> None:
    for model in REASONING:
        provider = OpenAIProvider(api_key="sk-test", model=model)
        assert provider._use_responses() is True, model


def test_api_key_route_keeps_chat_completions_for_plain_models() -> None:
    for model in PLAIN:
        provider = OpenAIProvider(api_key="sk-test", model=model)
        assert provider._use_responses() is False, model


def test_subscription_always_uses_responses() -> None:
    """Auth still forces the protocol one way: Codex only speaks Responses."""
    provider = OpenAIProvider(api_key=None, model="gpt-4o")
    with patch.object(provider, "_subscription_active", True):
        assert provider._use_responses() is True


def test_per_call_model_override_beats_the_constructor() -> None:
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
    assert provider._use_responses() is False
    assert provider._use_responses(model="gpt-5.6-luna") is True


# --- route selection (endpoint + headers) --------------------------------


def _captured_request(provider: OpenAIProvider, **kwargs):
    """Run one streaming request against a stubbed transport; return it."""
    seen = {}

    class _Resp:
        status_code = 200

        def iter_lines(self):
            return iter(())

        def close(self):
            pass

        def read(self):
            return b""

    class _Client:
        def build_request(self, method, url, headers=None, json=None):
            seen.update(url=url, headers=headers or {}, body=json or {})
            return object()

        def send(self, request, stream=False):
            return _Resp()

        def close(self):
            pass

    with patch("httpx.Client", return_value=_Client()):
        provider._subscription_stream_request(
            [{"role": "user", "content": "hi"}], None, **kwargs
        )
    return seen


def test_api_key_route_targets_the_public_responses_endpoint() -> None:
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-luna")
    seen = _captured_request(provider)
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"


def test_endpoint_is_derived_from_an_explicit_base_url() -> None:
    """Derived rather than hardcoded, so a trailing slash still resolves."""
    provider = OpenAIProvider(
        api_key="sk-test", model="gpt-5.6-luna", base_url="https://api.openai.com/v1/"
    )
    assert _captured_request(provider)["url"] == "https://api.openai.com/v1/responses"


def test_a_gateway_base_url_keeps_chat_completions() -> None:
    """A proxy may not implement /responses; switching would 404 it.

    `providers.openai.base_url` is user-configurable, so this provider is
    also the route to LiteLLM/vLLM/Azure-style proxies. Those speak Chat
    Completions universally, and the 400s motivating the Responses route are
    api.openai.com's own behaviour, not necessarily theirs.
    """
    for base in (
        "https://gw.example/v1",
        "http://localhost:4000/v1",
        "https://x.openai.azure.com/openai/deployments/d1",
    ):
        provider = OpenAIProvider(
            api_key="sk-test", model="gpt-5.6-luna", base_url=base
        )
        assert provider._use_responses() is False, base


def test_the_subscription_route_ignores_a_gateway_base_url() -> None:
    """Codex speaks only Responses, and always at its own endpoint."""
    provider = OpenAIProvider(api_key=None, model="gpt-5.6-luna",
                              base_url="https://gw.example/v1")
    with patch.object(provider, "_subscription_active", True):
        assert provider._use_responses() is True


def test_subscription_route_targets_the_codex_backend() -> None:
    provider = OpenAIProvider(api_key=None, model="gpt-5.6-luna")
    with patch.object(provider, "_subscription_active", True), patch(
        "src.auth.openai_subscription.get_valid_credentials",
        return_value=_credentials(),
    ):
        seen = _captured_request(provider)
    assert seen["url"] == CODEX_API_ENDPOINT
    assert seen["headers"]["Authorization"] == "Bearer access"


# --- reasoning payload gating --------------------------------------------


def test_reasoning_is_sent_only_to_models_that_have_it() -> None:
    """`reasoning.effort` on a plain model is a hard 400, so it must be absent."""
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-luna")
    body = provider._subscription_request_body(
        [{"role": "user", "content": "hi"}], None,
        extra_body={"reasoning_effort": "xhigh"},
    )
    assert body["reasoning"]["effort"] == "xhigh"

    plain = OpenAIProvider(api_key="sk-test", model="gpt-4o")
    body = plain._subscription_request_body(
        [{"role": "user", "content": "hi"}], None,
        extra_body={"reasoning_effort": "xhigh"},
    )
    assert "reasoning" not in body


def test_max_reaches_the_wire_as_xhigh_on_the_api_key_route() -> None:
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-luna")
    body = provider._subscription_request_body(
        [{"role": "user", "content": "hi"}], None,
        extra_body={"reasoning_effort": "max"},
    )
    assert body["reasoning"]["effort"] == "xhigh"


# --- Chat Completions fallback sanitising --------------------------------


def test_effort_is_stripped_before_a_plain_model_hits_chat_completions() -> None:
    """`--provider openai --model gpt-4o --effort high` used to 400 on call 1."""
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
    cleaned = provider._without_unsupported_reasoning(
        {"extra_body": {"reasoning_effort": "high"}}
    )
    assert "reasoning_effort" not in cleaned["extra_body"]


def test_stripping_preserves_other_extra_body_keys_and_the_original() -> None:
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
    original = {"extra_body": {"reasoning_effort": "high", "user": "u1"}}
    cleaned = provider._without_unsupported_reasoning(original)
    assert cleaned["extra_body"] == {"user": "u1"}
    # the caller's dict is not mutated out from under it
    assert original["extra_body"]["reasoning_effort"] == "high"


def test_reasoning_models_keep_their_effort_untouched() -> None:
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-luna")
    kwargs = {"extra_body": {"reasoning_effort": "xhigh"}}
    assert provider._without_unsupported_reasoning(kwargs)["extra_body"] == {
        "reasoning_effort": "xhigh"
    }


def test_kwargs_without_effort_are_passed_through_unchanged() -> None:
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
    kwargs = {"temperature": 0.5}
    assert provider._without_unsupported_reasoning(kwargs) is kwargs


# --- 401 handling --------------------------------------------------------


def test_api_key_401_reports_the_provider_error_not_a_login_prompt() -> None:
    """A rejected key is not an expired OAuth token; refreshing would mislead."""

    class _Resp:
        status_code = 401

        def close(self):
            pass

        def read(self):
            return b'{"error":{"message":"Incorrect API key provided"}}'

    class _Client:
        def build_request(self, *a, **k):
            return object()

        def send(self, request, stream=False):
            return _Resp()

        def close(self):
            pass

    provider = OpenAIProvider(api_key="sk-bad", model="gpt-5.6-luna")
    with patch("httpx.Client", return_value=_Client()), patch(
        "src.auth.openai_subscription.force_refresh"
    ) as refresh:
        try:
            provider._subscription_stream_request(
                [{"role": "user", "content": "hi"}], None
            )
        except RuntimeError as exc:
            message = str(exc)
        else:  # pragma: no cover - the stub always fails
            raise AssertionError("expected the 401 to surface")

    refresh.assert_not_called()
    assert "Incorrect API key provided" in message
    assert "login expired" not in message


# --- transport-drop retry ------------------------------------------------


def test_responses_path_retries_a_dropped_stream() -> None:
    """The Responses path must sit INSIDE the base class's retry loop.

    An un-retried "peer closed connection" ended 8 of 89 terminal-bench 2.1
    trials (see `OpenAICompatibleProvider.chat_stream_response`). Dispatching
    from `chat_stream_response` instead of `_stream_attempt` would opt every
    Responses request out of that loop without failing any other test.
    """
    import httpx

    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-luna")
    calls = []

    def _attempt(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            )
        return "recovered"

    with patch.object(provider, "_subscription_stream_request", _attempt):
        result = provider.chat_stream_response([{"role": "user", "content": "hi"}])

    assert result == "recovered"
    assert len(calls) == 2, "the dropped stream was not re-issued"


def test_a_server_verdict_is_not_retried_on_the_responses_path() -> None:
    """A 4xx is a decision, not a drop — re-issuing would double-bill it."""
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-luna")
    calls = []

    def _attempt(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("OpenAI API error (400): bad request")

    with patch.object(provider, "_subscription_stream_request", _attempt):
        try:
            provider.chat_stream_response([{"role": "user", "content": "hi"}])
        except RuntimeError:
            pass

    assert len(calls) == 1, "a 400 must not be re-issued"


# --- cost accounting -----------------------------------------------------


def test_api_key_usage_is_billed_and_subscription_usage_is_not() -> None:
    """`billing_mode` follows the AUTH mode, not the protocol.

    `cost_tracker.record_api_usage` zeroes the cost of any usage marked
    `billing_mode: subscription`. That is right for a flat-rate ChatGPT plan
    and wrong for a metered API key — and this protocol now carries both, so
    routing API-key traffic here without splitting the flag would report
    every OpenAI request as $0.00.
    """
    from src.providers.openai_responses import build_usage_dict

    raw = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    assert build_usage_dict(raw)["billing_mode"] == "subscription"
    assert "billing_mode" not in build_usage_dict(raw, subscription=False)


def test_billed_usage_produces_a_non_zero_cost() -> None:
    """End of the chain: the flag actually changes what `/cost` records."""
    from src.cost_tracker import compute_cost
    from src.providers.openai_responses import build_usage_dict

    billed = build_usage_dict(
        {"input_tokens": 100_000, "output_tokens": 50_000}, subscription=False
    )
    assert billed.get("billing_mode") != "subscription"
    # a metered key must be able to produce real cost; a zero here would mean
    # the usage shape never reaches the pricing table
    assert compute_cost("gpt-5.6-luna", billed) > 0


def test_token_counts_survive_either_billing_mode() -> None:
    """Cost is separate from the context-left display, which needs the counts."""
    raw = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    for kwargs in ({}, {"subscription": False}):
        from src.providers.openai_responses import build_usage_dict

        usage = build_usage_dict(raw, **kwargs)
        assert usage["input_tokens"] == 7
        assert usage["output_tokens"] == 3


# --- $OPENAI_BASE_URL ----------------------------------------------------


def test_env_base_url_keeps_reasoning_models_on_chat_completions(monkeypatch) -> None:
    """`self.base_url` is not the only channel — the SDK reads the env var.

    `set_api_key` writes the config key only when one is passed, so
    `base_url=None` with `$OPENAI_BASE_URL` exported is an ordinary state.
    Reading only the attribute would send Chat Completions traffic to the
    proxy while the default model went straight to api.openai.com, carrying
    the key and the conversation around a configured egress path.
    """
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.corp.internal/v1")
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.4")
    assert provider._is_first_party_base_url() is False
    assert provider._use_responses() is False


def test_env_base_url_pointing_at_openai_still_uses_responses(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.4")
    assert provider._use_responses() is True


def test_an_explicit_base_url_beats_the_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.corp.internal/v1")
    provider = OpenAIProvider(
        api_key="sk-test", model="gpt-5.4", base_url="https://api.openai.com/v1"
    )
    assert provider._use_responses() is True


# --- max_output_tokens ---------------------------------------------------


def test_max_tokens_reaches_the_wire_on_the_api_key_route() -> None:
    """Callers pass `max_tokens` as a BOUND, not a preference.

    Compaction summaries, the permission classifier, the /goal judge and the
    advisor all set one. Dropping it silently unbounds them — and query.py's
    `max_output_tokens_escalate` lane re-issues with a larger value after a
    truncated reply, which without this is byte-identical to the request that
    just truncated.
    """
    provider = OpenAIProvider(api_key="sk-test", model="gpt-5.6-luna")
    body = provider._subscription_request_body(
        [{"role": "user", "content": "hi"}], None, max_tokens=64_000
    )
    assert body["max_output_tokens"] == 64_000


def test_the_subscription_route_still_omits_max_tokens() -> None:
    """The Codex backend rejects it — that carve-out is route-specific."""
    provider = OpenAIProvider(api_key=None, model="gpt-5.6-luna")
    with patch.object(provider, "_subscription_active", True):
        body = provider._subscription_request_body(
            [{"role": "user", "content": "hi"}], None, max_tokens=64_000
        )
    assert "max_output_tokens" not in body


# --- error classification ------------------------------------------------


def test_a_429_is_classified_as_retryable_not_as_a_generic_failure() -> None:
    """The retry layer classifies by ATTRIBUTE, not by message text.

    A bare RuntimeError carries no `status_code`, so a 429 read as a
    permanent failure: the request was abandoned rather than backed off,
    and the server's `Retry-After` was discarded.
    """
    from src.providers.openai_provider import ResponsesHTTPError
    from src.services.api.errors import is_overloaded_error, is_rate_limit_error

    assert is_rate_limit_error(
        ResponsesHTTPError("OpenAI API error (429): slow down", status_code=429)
    )
    assert is_overloaded_error(
        ResponsesHTTPError("OpenAI API error (529): busy", status_code=529)
    )
    # the shape it replaced classified as neither
    assert not is_rate_limit_error(RuntimeError("OpenAI API error (429): slow down"))


def test_the_http_error_stays_catchable_as_a_runtime_error() -> None:
    """Existing `except RuntimeError` handlers must keep working."""
    from src.providers.openai_provider import ResponsesHTTPError

    assert isinstance(
        ResponsesHTTPError("x", status_code=500), RuntimeError
    )


def test_retry_after_is_reachable_from_the_error() -> None:
    """`_retry_after_seconds` reads `e.response.headers`."""
    from src.query.query import _retry_after_seconds
    from src.providers.openai_provider import ResponsesHTTPError

    class _Resp:
        headers = {"retry-after": "7"}

    err = ResponsesHTTPError("429", status_code=429, response=_Resp())
    assert _retry_after_seconds(err, default=1.0) == 7.0


# --- OAuth eligibility ---------------------------------------------------


def test_a_proxy_in_the_env_disables_the_oauth_fallback(monkeypatch) -> None:
    """An OAuth session cannot be proxied, so it must not silently activate.

    `oauth_eligible` derived the first-party rule a second time from the
    constructor PARAMETER, so it never learned about `$OPENAI_BASE_URL`.
    With a proxy in the env, no API key and a stored ChatGPT login, prompts
    went to chatgpt.com with no error — the outcome the guard exists to
    prevent. It now shares `_is_first_party_base_url`.
    """
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.corp.internal/v1")
    creds = _credentials()
    with patch.object(auth, "load_credentials", return_value=creds):
        provider = OpenAIProvider(api_key=None, model="gpt-5.4")
    assert provider._subscription_active is False


def test_the_oauth_fallback_still_activates_without_a_proxy(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    creds = _credentials()
    with patch.object(auth, "load_credentials", return_value=creds):
        provider = OpenAIProvider(api_key=None, model="gpt-5.4")
    assert provider._subscription_active is True


def test_an_api_key_still_beats_a_stored_login(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    creds = _credentials()
    with patch.object(auth, "load_credentials", return_value=creds):
        provider = OpenAIProvider(api_key="sk-test", model="gpt-5.4")
    assert provider._subscription_active is False
